# Go2-W onboard deployment snapshot

- Snapshot date: 2026-08-25
- Source host: Unitree Go2-W onboard Ubuntu
- Source directory: `/home/unitree/robotscene`
- Git branch: `robot-go2w-deployment-20260825`

This branch records the source code and configuration actually present on the
robot. It was prepared in a temporary clone and does not contain changes from
the developer workstation's active branch.

The snapshot intentionally excludes credentials and generated/runtime data:

- `.env` and other secret-bearing environment files
- Python virtual environments and caches
- ROS/catkin `build`, `install`, `log`, and `devel` directories
- runtime frames, session output, logs, backups, rosbags, and model weights

`.env.go2w` and `.env.example` are included because they contain deployment
settings but no API secret values.
