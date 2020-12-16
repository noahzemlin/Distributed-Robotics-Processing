# Distributed Robotics Processing

## Description

Autonomous mobile robots require the successful coordination of many tasks to accomplish their goals and move about their environments. These tasks range from computing obstacles from LIDAR to performing state estimation using Kalman Filters. These tasks share very little computing steps but often share lots of data such as the robot's state. In this project, I create a distributed robotics processing system that allows for sharing of data, fault tolerance, and low latency.

## Problem 1: State Replication

The first distributed problem this project faces is the need to share data across the nodes in a way that each node has consistent information with other nodes. There are many ways to solve this problem, such as shared data spaces, but I choose to go with an event-based publisher-subscriber model. This model allows nodes to publish and subscribe to specific data events so that each node only has to send and receive the data it is responsible for. This allows for a separation of processing and coordination [1] that matches the inherit separation of computing and data of the problem.

In my solution, the robot hosts a server through which all other nodes connect to via TCP. The server keeps track of which nodes are subscribed to which topics. Each node can then tell the server if it would like to subscribe to a topic. When a node publishes data to a topic, all subscribed nodes are then sent that data. This method allows all nodes that need the same data to always receive the same data at the same time.

## Problem 2: Fault Tolerance

Mobile robotics can drastically differ in their safety requirements, but almost all of them require a robust handling of failures. If a step in the process fails, such as the path planning or state estimation, the robot can no longer be sure the actions it is taking are correct. Therefore, a mobile robotics system must be able to detect faults and handle them consistently to prevent the robot from performing bad or potentially dangerous actions.

In this project, by having the robot server be the source of all traffic between nodes, the server can also tell when a node fails. If the connection to a node is lost, often via power loss or network failure, the server is able to detect this because it is using a TCP connection. Further, if a node begins to send malformed data due to some corruption, the server is also able to detect these errors. If the nodes spoke to each other without talking to the robot, each node would be independently responsible for evaluating the faults of the nodes it receives from which could create a Byzantine General Problem where it is unknown where multiple failing nodes may fail to relay a fault to the robot.

## Problem 3: Latency

The final problem for this project is the latency associated with propagating data through a distributed network. An autonomous mobile robot must be able to quickly react to changes in its environment. Because each task the nodes process may rely on several tasks before it, there is an inherit propagation delay of the data from the first task to actually sending instructions to the robot. For example, the LIDAR data first must be processed into specific obstacle/landmark detections. These detections are then fed to SLAM (Simultaneous Localization and Mapping), which then feeds the Kalman Filter, which then feeds the path planning, which then gives the robot instructions. If each node is hosted on separate machines with non-trivial latencies between them, then there can be significant amounts of waiting on each node to receive data from the previous nodes.

Two solutions were implemented to help reduce latency. First, because of the publisher-subscriber model, each node can pick which data it receives from which nodes and data does not have to flow through one path of nodes. For example, the final node responsible for creating instructions for the robot may both receive paths from path planning (which will have large propagation delay as described earlier) but also obstacles from the obstacle detection (which will have less propagation delay as it feeds directly from the sensors). This allows the developer to choose a trade-off between the accuracy of the longer data paths with the reduced latency of shorter data paths. For example, choosing to use obstacle detection data when an obstacle is very close and path planning otherwise.

The second solution is by having the nodes continuously work without blocking for new data. While this means that nodes may be performing work on stale data, other nodes that use their data may be joining it with data on a shorter data path. This allows those other nodes to receive a mixture of stale and newer data to perform processing which, while potentially less correct, will allow more frequent updates and less latency.

## Evaluation

To evaluate the project, I ran a few tests created to demonstrate the solutions to the problems described

### Test 1

# Description of code base



# Sources

[1] M. van Steen and A.S. Tanenbaum, Distributed Systems, 3rd ed., distributed-systems.net, 2017.