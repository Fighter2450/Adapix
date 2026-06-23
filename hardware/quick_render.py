"""4-angle review render of the v6 soft-brutalist case.

Renders the assembled STL from four angles with ceramic-matte shading,
backface culling, and painter sort. Output: adapix_v14_review.png
"""
import os, math
os.environ.setdefault("MPLBACKEND", "Agg")
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
mesh = trimesh.load(os.path.join(HERE, "adapix_assembled.stl"))
print(f"loaded {len(mesh.vertices)} verts / {len(mesh.faces)} faces")

V = mesh.vertices
F = mesh.faces
N = mesh.face_normals
CTR = V[F].mean(axis=1)

# Ceramic warm off-white (soft brutalist material)
BASE = np.array([0.88, 0.82, 0.72])
BG   = "#0c1118"
TXT  = "#9fb0c8"


def cam_dir(elev, azim):
    e = math.radians(elev); a = math.radians(azim)
    return np.array([math.cos(e) * math.cos(a),
                     math.cos(e) * math.sin(a),
                     math.sin(e)])


def render(ax, elev, azim, key=(0.7, -0.5, 0.85)):
    cam = cam_dir(elev, azim)
    vis = (N @ cam) > 0.02
    tris = V[F[vis]]
    nrm  = N[vis]
    ctr  = CTR[vis]
    k = np.array(key, float); k /= np.linalg.norm(k)
    sh = np.clip(nrm @ k, 0, 1)
    # rim from a soft cyan back-light
    rim = np.clip(nrm @ np.array([-0.3, 0.5, 0.4]), 0, 1) * 0.35
    inten = 0.22 + 0.65 * sh
    cols = np.outer(inten, BASE)
    # add cyan rim
    cyan = np.array([0.0, 0.55, 0.85])
    cols += np.outer(rim, cyan) * 0.4
    cols = np.clip(cols, 0, 1)
    cols = np.column_stack([cols, np.ones(len(cols))])
    order = np.argsort(ctr @ cam)
    coll = Poly3DCollection(tris[order], facecolors=cols[order],
                            edgecolors=(0.05, 0.05, 0.08, 0.18),
                            linewidths=0.10)
    ax.add_collection3d(coll)
    mn, mx = mesh.bounds
    pad = 5
    ax.set_xlim(mn[0]-pad, mx[0]+pad)
    ax.set_ylim(mn[1]-pad, mx[1]+pad)
    ax.set_zlim(mn[2]-pad, mx[2]+pad)
    ax.set_box_aspect((mx[0]-mn[0], mx[1]-mn[1], mx[2]-mn[2]))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    ax.set_facecolor(BG)


fig = plt.figure(figsize=(20, 14), facecolor=BG)

ax1 = fig.add_subplot(2, 2, 1, projection="3d")
ax1.set_title("HERO  -  front 3/4 (sloped top + LCD)", color=TXT, fontsize=12, pad=4)
render(ax1, elev=18, azim=-128)

ax2 = fig.add_subplot(2, 2, 2, projection="3d")
ax2.set_title("BACK 3/4  -  shows port wall + slope", color=TXT, fontsize=12, pad=4)
render(ax2, elev=22, azim=-40)

ax3 = fig.add_subplot(2, 2, 3, projection="3d")
ax3.set_title("PLAN  -  top down (LCD on slope + R22 corners)", color=TXT, fontsize=12, pad=4)
render(ax3, elev=82, azim=-90)

ax4 = fig.add_subplot(2, 2, 4, projection="3d")
ax4.set_title("PROFILE  -  side (wedge profile)", color=TXT, fontsize=12, pad=4)
render(ax4, elev=4, azim=180)

plt.subplots_adjust(left=0, right=1, top=0.96, bottom=0, wspace=0, hspace=0.06)
out = os.path.join(HERE, "adapix_v14_review.png")
plt.savefig(out, dpi=140, facecolor=BG, bbox_inches="tight")
print(f"wrote {out}")
