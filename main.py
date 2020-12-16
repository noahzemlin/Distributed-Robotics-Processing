import logging
import time
import sys
import os
import random
import select
import socket
import struct
from queue import Queue, Empty
from enum import Enum
from threading import Thread, Lock

# Configuration
HOST = "127.0.0.1"
PORT = 9000 + random.randint(1, 100)

# Define Enums used for networking
class TopicType(Enum):
    STATE = 0
    EKF_OUT = 1
    COMMAND = 2

class MessageType(Enum):
    ACK = 0
    NACK = 1
    SUBSCRIBE = 2
    PUBLISH = 3

    @staticmethod
    def length():
        return struct.calcsize('I I 128s')

    @staticmethod
    def pack(msg_type, topic, data=b""):
        return struct.pack('I I 128s', msg_type.value, topic.value, data)

    @staticmethod
    def unpack(data):
        return struct.unpack('I I 128s', data)

class ServiceServer(Thread):
    def __init__(self, logger, recv_func):
        '''
        Server thread that is ran on the robot

        logger: logger to log info,errors,warnings to
        recv_func: function that is called when a subscribed topic is published to
        '''
        Thread.__init__(self)

        self.logger = logger
        self.recv_func = recv_func

        # TCP socket server
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("0.0.0.0", PORT))
        self.socket.listen(5)
        self.socket.setblocking(0)
        self.subscriptions = {}

        # Release to stop thread
        self.running = Lock()
        self.running.acquire()

    def subscribe_client(self, client, topic):
        '''
        Subscribe a client to a topic id

        client: socket of client
        topic: topic number from TopicType
        '''
        if topic in self.subscriptions:
            self.subscriptions[topic].append(client)
        else:
            self.subscriptions[topic] = [client]

    def publish_data(self, topic, data):
        '''
        Publish data to all subscribed clients

        topic: topic number from TopicType
        data: data bytes to send
        '''
        if topic in self.subscriptions:
            for s in self.subscriptions[topic]:
                if s is self.socket:
                    self.recv_func(MessageType.unpack(data))
                else:
                    self.message_queues[s].put(data)

                    if s not in self.outputs:
                        self.outputs.append(s)

    def run(self):
        '''Ran when thread is started'''
        inputs = [ self.socket ]
        self.outputs = [ ]
        self.message_queues = {}

        # Adapated from https://pymotw.com/2/select/
        while inputs and self.running.locked():
            # We are using nonblocking TCP, so we use select (with 1 second timeout)
            readable, writable, _ = select.select(inputs, self.outputs, inputs, 1)

            for s in readable:
                if s is self.socket:
                    # The server socket is readable, this means we have a new connection

                    # Accept new connection
                    connection, client_address = s.accept()
                    self.logger.info(f'{client_address} connected.')
                    connection.setblocking(0)

                    # Add to input list to track incoming messages and prepare queue for outgoing messages
                    inputs.append(connection)
                    self.message_queues[connection] = Queue()
                else:
                    # A client has sent data!

                    # Read the data
                    data = s.recv(MessageType.length())
                    if data:
                        # If there is data, unpack it and handle it based on the message type
                        msg_type, topic, _ = MessageType.unpack(data)

                        self.logger.info(f"Received type {msg_type} with topic {topic}")

                        if msg_type == MessageType.SUBSCRIBE.value:
                            # Client is asking to subscribe to topic
                            self.subscribe_client(s, topic)
                        elif msg_type == MessageType.PUBLISH.value:
                            # Client is publishing a message, relay to other clients (and robot)
                            if topic in self.subscriptions:
                                self.publish_data(topic, data)
                            else:
                                self.logger.warning(f'{client_address} attempting to publish to topic {topic} which has no subscribers.')
                        else:
                            self.logger.error(f'Received unhandled message type {msg_type}')
                    else:
                        # If not data, then the client closed the connection
                        self.logger.info(f'{client_address} closed.')
                        
                        # Remove the client from lists, close socket, and delete queue
                        if s in self.outputs:
                            self.outputs.remove(s)
                        inputs.remove(s)
                        s.close()
                        del self.message_queues[s]
    
            # Handle sending data when we are ready to
            for s in writable:
                try:
                    # Are there messages in the queue?
                    next_msg = self.message_queues[s].get_nowait()
                except Empty:
                    # No, there are no messages. Remove from outputs and wait for queue to be populated again
                    self.outputs.remove(s)
                else:
                    # Yes, it is! Send the message
                    s.send(next_msg)

        # Close socket at end
        self.socket.close()

class ClientSocket(Thread):
    def __init__(self, logger, recv_func):
        '''
        Client thread that is ran on nodes

        logger: logger to log info,errors,warnings to
        recv_func: function that is called when a subscribed topic is published to
        '''
        Thread.__init__(self)
        self.logger = logger
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.recv_func = recv_func

        # Release to stop thread
        self.running = Lock()
        self.running.acquire()
    
    def send(self, msg_type, topic, data=b""):
        '''
        Send message to the server

        msg_type: message type from MessageType
        topic: topic from TopicType
        data: data bytes to send
        '''
        self.socket.send(MessageType.pack(msg_type, topic, data))
    
    def run(self):
        '''Ran when thread is started'''
        # Connect to robot server
        self.socket.connect((HOST, PORT))

        # As long as we are still running, wait for new data and handle it
        while self.running.locked():
            msg = self.socket.recv(MessageType.length())
            self.recv_func(MessageType.unpack(msg))

        self.socket.close()

class Machine(Thread):
    def __init__(self, machine_name):
        '''
        Node thread that actually runs the node's code

        machine_name: name for logging
        '''
        Thread.__init__(self)

        # Setup logging (https://docs.python.org/3/howto/logging-cookbook.html)
        self.logger = logging.getLogger(machine_name)
        self.logger.setLevel(logging.DEBUG)
        fh = logging.FileHandler(machine_name + '.log')
        fh.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG)
        formatter = logging.Formatter('[ %(asctime)s %(levelname)s %(name)s ] %(message)s', datefmt='%I:%M:%S')
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)
        self.logger.addHandler(fh)
        self.logger.addHandler(ch)

        # Create client socket
        self.socket = ClientSocket(self.logger, self.recv)

        # State to be replicated
        self.state = {
            "x":0,
            "y":0,
            "x_dot":0,
            "y_dot":0
        }

        # Running Lock
        self.running = Lock()
        self.running.acquire()

    def subscribe(self, topic):
        '''Subscribe to a topic'''
        self.socket.send(MessageType.SUBSCRIBE, topic)

    def publish(self, topic, data):
        '''Publish to a topic'''
        self.socket.send(MessageType.PUBLISH, topic, data)

    def recv(self):
        '''Override with receiving logic when subscribing'''
        pass

class M_Robot(Machine):
    def __init__(self):
        '''Robot thread'''
        Machine.__init__(self, "Robot")
        self.logger.info("Started")

    def recv(self, recv_vals):
        '''Handle receiving messages'''
        (msg_type, topic, data) = recv_vals

        if topic == TopicType.COMMAND.value:
            l,r = struct.unpack("f f 120x", data)
            self.logger.info(f"Setting motor speeds to {l},{r}")

    def run(self):
        '''Ran when thread is started'''
        self.logger.info("Running")
        self.server = ServiceServer(self.logger, self.recv)
        self.server.start()

        # Subscribe robot to movement commands
        # This is kinda hacky and prob could be improved
        self.server.subscriptions[TopicType.COMMAND.value] = [self.server.socket]

        while self.running.locked():
            time.sleep(0.2)
            self.state["x"] = self.state["x"] + random.random() + 0.2
            self.state["y"] = self.state["y"] + random.random() + 0.6
            self.server.publish_data(TopicType.STATE.value, MessageType.pack(MessageType.PUBLISH, TopicType.STATE, struct.pack('f f f f 112x', self.state["x"], self.state["y"], self.state["x_dot"], self.state["y_dot"])))

        self.server.running.release()
        self.logger.info("Done!")

class M_EKF(Machine):
    def __init__(self):
        '''EKF thread'''
        Machine.__init__(self, "EKF")
        self.logger.info("Started")

    def recv(self, recv_vals):
        '''Handle receiving messages'''
        (msg_type, topic, data) = recv_vals

        if topic == TopicType.STATE.value:
            # Update state based on incoming update (only care about x and y here, we compute x_dot an y_dot)
            x,y,_,_ = struct.unpack('f f f f 112x', data)
            self.state["x"] = x
            self.state["y"] = y
        
    def run(self):
        '''Ran when thread is started'''
        self.logger.info("Running")
        self.socket.start()

        # Subscribe to receive state updates
        self.subscribe(TopicType.STATE)
        
        last_x = self.state["x"]
        last_y = self.state["y"]
        while self.running.locked():
            time.sleep(0.5)
            # This is the worst EKF ever, but good enough for this project
            self.state["x_dot"] = (self.state["x"] - last_x) / 0.5
            self.state["y_dot"] = (self.state["y"] - last_y) / 0.5
            last_x = self.state["x"]
            last_y = self.state["y"]
            self.publish(TopicType.EKF_OUT, struct.pack('f f f f 112x', self.state["x"], self.state["y"], self.state["x_dot"], self.state["y_dot"]))
            self.logger.info(f"New state: {self.state}")

        self.socket.running.release()
        self.logger.info("Done!")

class M_PathPlanning(Machine):
    def __init__(self):
        '''Path planning thread'''
        Machine.__init__(self, "PathPlanning")
        self.logger.info("Started")

    def recv(self, recv_vals):
        '''Handle receiving messages'''
        (msg_type, topic, data) = recv_vals

        if topic == TopicType.EKF_OUT.value:
            # Update state based on incoming update
            x,y,x_dot,y_dot = struct.unpack('f f f f 112x', data)
            self.state = {
                "x":x,
                "y":y,
                "x_dot":x_dot,
                "y_dot":y_dot
            }
        
    def run(self):
        '''Ran when thread is started'''
        self.logger.info("Running")
        self.socket.start()

        # Subscribe to receive state updates
        self.subscribe(TopicType.EKF_OUT)
        
        while self.running.locked():
            time.sleep(0.5)

            # Create command based on state
            self.publish(TopicType.COMMAND, struct.pack('f f 120x', -self.state["x_dot"]/10, -self.state["y_dot"]/10))

        self.socket.running.release()
        self.logger.info("Done!")

# Globally defined threads (for handling KeyboardInterrupt)
threads = {
    "M_Robot": M_Robot(),
    "M_EKF": M_EKF(),
    "M_PathPlanning": M_PathPlanning()
}

def main():
    print("Starting threads...")

    for thrd in threads:
        threads[thrd].start()
        time.sleep(1) # Wait to give time for each thread to setup

    print("Threads started!")

    for thrd in threads:
        threads[thrd].join()

    print("Threads finished!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print('\nKeyboard Interrupt Called')
        try:
            # On keyboard interrupt, stop all threads
            for thrd in threads:
                threads[thrd].running.release()
        except SystemExit:
            os._exit(0)