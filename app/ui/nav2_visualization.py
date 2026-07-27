"""Matplotlib visualization for ROS-independent Nav2 payloads."""
from __future__ import annotations

def render_nav2_path_figure(path: dict, current_pose: dict | None = None):
    from matplotlib.figure import Figure
    poses=path.get("poses", [])
    figure=Figure(figsize=(7,5)); axis=figure.subplots()
    if poses:
        axis.plot([p["x"] for p in poses],[p["y"] for p in poses],"-o",markersize=3,label="Planned Path")
        axis.scatter([poses[0]["x"]],[poses[0]["y"]],color="green",s=70,label="Start")
        axis.scatter([poses[-1]["x"]],[poses[-1]["y"]],color="red",s=100,marker="*",label="Goal")
    if current_pose:
        axis.scatter([current_pose["x"]],[current_pose["y"]],color="orange",s=70,label="Current Pose")
    axis.set_xlabel("X (m)"); axis.set_ylabel("Y (m)"); axis.set_aspect("equal",adjustable="datalim")
    axis.grid(True); axis.legend()
    return figure
