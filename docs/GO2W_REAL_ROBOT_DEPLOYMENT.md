# Go2-W built-in RGB + LiDAR deployment

Status date: 2026-08-06 (Asia/Shanghai)

> 2026-08-13 更新：本文档部分内容已被 README 顶部的
> “Go2-W 真机项目当前进度还原指南”取代（小范围运动已授权并完成 LLM 搜索、
> wheel+LIO 融合里程计等）。本文保留为只读部署参考。

This deployment is constrained to a stationary robot. The commands in the
read-only sections do not start Sport, a lease client, `/cmd_vel`, Nav2,
posture control, or joint control. Calibration may move a printed target by
hand; it must not move the robot.

## Proven manufacturer geometry

The pinned manufacturer reference is
`configs/go2w/official_reference.yaml`:

- standing envelope: `0.70 x 0.43 x 0.50 m`;
- nominal tire diameter: 7 inches;
- `base_link -> utlidar_lidar`: xyz `(0.28945, 0, -0.046825) m`,
  rpy `(0, 2.8782, 0) rad`;
- `utlidar_lidar -> utlidar_imu`: xyz
  `(-0.007698, -0.014655, 0.00667) m`, aligned axes.

These values come from a pinned Unitree Go2-W URDF/product reference and the
Unitree LiDAR SDK. They do not supply camera intrinsics or the camera pose.

## Network preflight

The dedicated interface must have physical carrier and a host address in
`192.168.123.0/24`:

```bash
ip -brief link show enp6s0
ip -4 -brief address show enp6s0
```

`scripts/go2w/start_live_perception.sh` now refuses to start if carrier or the
host subnet address is absent. It does not configure the interface silently.

## Build and read-only perception

```bash
bash scripts/go2w/install_dependencies.sh
bash scripts/go2w/build_ros2.sh
bash scripts/go2w/start_live_perception.sh
```

The production camera source is the read-only VideoHub image RPC. The custom
`/frontvideostream` H.264 path remains diagnostic-only because fresh trials
showed damaged DDS samples and 99.4% solid-green decoded frames.

The live Bundle Worker emits at 1 Hz and retains at most 30 Bundles per session
in the transient spool. This keeps the queue bounded. Session evidence belongs
under `outputs/live_sessions/` or a named acceptance directory, not in an
unbounded frame spool.

Run the required ten-minute stationary transport soak with:

```bash
bash scripts/go2w/run_level_a_acceptance.sh
```

The first 600-second attempt did not pass: Ethernet carrier disappeared around
the 423rd Bundle. Its RSS slope was stable and the spool stayed bounded, but a
complete ten-minute stream was not proven. That historical failure was
superseded by the passing corrected run below. Even a transport PASS does not
complete Level A until the camera TF is physically measured.

After carrier restoration and runner session-isolation/rate-scheduling fixes,
the final 603.24-second rerun passed all transport checks: 489 Bundles at
0.816 Hz, 598.00 seconds of stamp coverage, 0.354-second final frame age,
30 retained Bundles/8.49 MiB, stable RSS slope, continuous carrier, and complete
owned-process cleanup. Its only Level-A blocker is now
`camera_tf_not_validated`. Evidence:
`outputs/go2w_acceptance/level_a_stationary_soak_fixed/result.json`.

## Camera calibration

Prepare a real chessboard and measure:

- inner corner count (`COLSxROWS`);
- square edge length in metres;
- operator identity.

Keep the Go2-W stationary and move only the board:

```bash
bash scripts/go2w/calibrate_camera.sh \
  --board COLSxROWS \
  --square-m MEASURED_METRES \
  --operator OPERATOR
```

Collect different distances, tilts, and image positions. Save the ROS
calibrator output, inspect `ost.yaml`, then install it:

```bash
bash scripts/go2w/calibrate_camera.sh \
  --board COLSxROWS \
  --square-m MEASURED_METRES \
  --operator OPERATOR \
  --install-from /path/to/ost.yaml
```

The installer rejects missing/non-finite coefficients and non-positive focal
lengths. Never replace this step with online camera guesses.

The current installed calibration used a measured 9x6-inner-corner board with
15 mm squares and 105 accepted views. Ten live nonzero-K CameraInfo triples
passed, followed by a separate-frame PnP sanity check with 0.859 px mean and
1.024 px RMS reprojection error. Evidence is under
`outputs/go2w_acceptance/camera_calibration_20260806/`. Recalibration is not the
current blocker; camera pose and RGB-LiDAR extrinsics are.

## RGB-LiDAR candidate extrinsics

After CameraInfo is calibrated, start the read-only perception stack in one
terminal. In another terminal, record a static structural scene containing
walls, door frames, table edges, or pillars:

```bash
bash scripts/go2w/record_extrinsic_calibration.sh \
  outputs/calibration/near_01 20 OPERATOR near_01
```

Repeat for near, medium, and far scenes. Extract synchronized scenes:

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 scripts/go2w/extract_extrinsic_calibration_dataset.py \
  --bag outputs/calibration/near_01/rosbag \
  --output-dir outputs/calibration/near_01_dataset
```

For every extracted scene, edit `correspondences.yaml`: set `distance_band` and
add structural pairs using a LiDAR `point_index` and the matching image pixel
`image_px`. Then estimate a candidate:

```bash
/usr/bin/python3 scripts/go2w/calibrate_rgb_lidar_extrinsics.py \
  --camera configs/go2w/camera_intrinsics.yaml \
  --scene outputs/calibration/near_01_dataset/scene_001 \
  --scene outputs/calibration/medium_01_dataset/scene_001 \
  --scene outputs/calibration/far_01_dataset/scene_001 \
  --operator OPERATOR \
  --output-dir outputs/calibration/pnp_candidate
```

The command writes a candidate YAML, depth-coloured overlays, and reprojection
metrics. It deliberately sets `candidate_unvalidated`, `confirmed: false`,
`authorizes_fusion: false`, and `authorizes_motion: false`.

The installed ROS fusion node remains useful before calibration because it
publishes explicit closed gates instead of fabricating 3D output. Its current
loopback-only acceptance result is:

```text
outputs/go2w_acceptance/rgb_lidar_fusion_blocked_runtime/result.json
fusion_ready=false
rgb_lidar_extrinsics_validated=false
authorizes_motion=false
```

After a future physical calibration passes, relative fusion uses the confirmed
LiDAR-to-camera transform directly. The optional odom output additionally
requires the real LiDAR/odom TF chain; no camera pose is guessed.

Final promotion requires at least five overlay scenes, near/medium/far bands,
mean edge error at or below the configured threshold, and a recheck after the
robot has moved to another position. The last condition is prohibited in this
stationary session, so RGB-LiDAR fusion remains blocked:

```bash
bash scripts/go2w/validate_rgb_lidar_overlay.sh
```

## Search and navigation gates

Stationary observation entry point:

```bash
bash scripts/go2w/start_search_session.sh --target "红色背包" --mode observe_only
```

Motion and Nav2 remain disabled. Current planner-only and execution gate
evidence is under `outputs/go2w_acceptance/navigation_gate/`. Do not run motion
acceptance, SLAM mapping, Nav2 execution, Cancel/STOP motion trials, or a
moved-position extrinsic recheck under the no-movement constraint.

## Evidence and reports

- deployment status: `reports/go2w_robot_scene_demo_deployment_report.md`;
- requirement audit: `reports/go2w_plan_completion_audit.md`;
- live evidence: `outputs/go2w_acceptance/`;
- ROS logs: `runtime/go2w/sessions/`;
- transient latest-frame spool: `runtime/go2w/spool/`.
