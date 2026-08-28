# Go2-W onboard deployment snapshot — 2026-08-28

This branch is a snapshot of the code deployed under
`/home/unitree/robotscene` on the Unitree Go2-W Ubuntu computer.

- Branch: `robot-go2w-deployment-20260828`
- Parent snapshot: `robot-go2w-deployment-20260825`
- Robot address used by deployment: `192.168.123.18`
- ROS motion workspace: `unitree_go2w_control/ros2_ws`
- Motion transport: lease-owning Python SDK executor with ROS 2 Action facade

The snapshot includes project source, robot configuration, launch scripts,
tests, deployment documentation, the motion-control source tree, and compact
acceptance evidence. It intentionally excludes credentials, virtual
environments, generated build/install trees, runtime sessions, sensor output,
logs, backups, model weights, and nested Git metadata.

Notable changes since the 2026-08-25 snapshot include signed wheel-encoder
translation verification, fail-closed backward breadcrumb recovery, active
zero-velocity braking, STOP timeout/stationary reconciliation, topology route
execution, recovery management, and associated tests.
