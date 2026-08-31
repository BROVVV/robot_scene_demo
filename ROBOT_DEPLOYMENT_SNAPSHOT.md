# Go2-W onboard deployment snapshot — 2026-08-31

This branch is a sanitized snapshot of the code deployed under
`/home/unitree/robotscene` on the Unitree Go2-W Ubuntu computer.

- Branch: `robot-go2w-deployment-20260831`
- Parent snapshot: `robot-go2w-deployment-20260828`
- Robot address used by deployment: `192.168.123.18`
- ROS motion workspace: `unitree_go2w_control/ros2_ws`
- Motion transport: lease-owning Python SDK executor with ROS 2 Action facade

The snapshot includes project source, robot configuration, launch scripts,
tests, deployment documentation, the motion-control source tree, and compact
acceptance evidence. It intentionally excludes credentials, virtual
environments, generated build/install/devel trees, runtime sessions, sensor
output, logs, backups, model weights, and nested Git metadata.

Notable changes since the 2026-08-28 snapshot include the VLM-only low-latency
semantic observation pipeline, the long-running SiliconFlow VLM daemon and
Unix-socket protocol, asynchronous semantic observation, latency profiling,
fresh-frame verification, revised runtime budgets, and related regression
tests. The robot launchers route external API traffic through the persistent
reverse SOCKS tunnel supplied by the operator PC and fail closed when that
tunnel is unavailable.
