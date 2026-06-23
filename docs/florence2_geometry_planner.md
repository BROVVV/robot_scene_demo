# Florence-2 Geometry Planner Notes

This demo adds an optional perception-to-navigation chain:

```text
RGB image
-> Florence-2 object detection
-> monocular geometry point map / depth
-> BEV occupancy / free space / ESDF
-> object bbox to BEV waypoint
-> A* local path
-> ROS2 /cmd_vel dry-run JSON
```

## Backends

- `llm`, `grounded_sam`, and `mock` remain available.
- `florence2` runs through `app/detectors/florence2_worker.py` in a subprocess.
- Real Florence-2 should run in a Python 3.11 worker environment configured by `FLORENCE2_PYTHON`.
- Real Florence-2 requires optional packages from `requirements-florence2.txt`.
- `FLORENCE2_ALLOW_MOCK=true` enables a deterministic smoke fallback when the model is unavailable.

## Geometry

`build_scene_geometry()` writes:

- `point_map.npy`
- `depth.npy`
- `bev_occupancy.png`
- `free_space_mask.png`
- `esdf.npy`
- `esdf.png`
- `bev_metadata.json`
- `geometry_debug.png`

If MoGe is not configured, the system uses `heuristic_fallback` and marks:

```json
{
  "metric_reliable": false,
  "warning": "Using heuristic geometry fallback; not metric-safe."
}
```

This fallback is for demo and unit tests only.

## Object Goals

`project_objects_to_bev()` samples valid 3D points in each bbox crop, estimates a median object center, then writes `bev_x`, `bev_y`, `distance_m`, `bearing_deg`, `clearance_m`, `reachable`, `goal_bev_x`, and `goal_bev_y`.

If an object center falls in occupied or unknown space, the goal is adjusted to the nearest free BEV cell.

## Local Planner

`plan_to_goal()` uses A* on the ESDF. Free space has positive ESDF, and occupied/unknown/out-of-bounds space is treated as collision. If a full path is unavailable and partial planning is enabled, the planner returns the best progress path.

The path is converted to the existing `Ros2MotionPlan` schema. It stays dry-run by default.

## Safety

Do not use heuristic geometry or single-image monocular estimates as a real robot safety stack. Real deployment must use metric depth, SLAM/localization, obstacle avoidance, emergency stop, speed limits, and platform vendor safety controls.
