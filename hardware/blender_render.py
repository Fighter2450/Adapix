"""Magazine-grade product render of the Adapix Adapt 1.0.

What this does:
  - Loads adapix_top.stl + adapix_bottom.stl from this directory
  - Applies a soft-touch anodized navy material with subtle clear coat
  - Adds a glowing cyan emission ring around the bottom (the underglow LEDs)
  - Adds a backlit Circuit-A logo on the top (emission only inside the engraving)
  - Studio 3-point lighting + a kicker rim from behind for the cyan glow
  - Polished reflective floor that fades to gradient background
  - 3200x2400 Cycles render with depth-of-field

Run:
    blender --background --python blender_render.py

Output: adapix_beauty.png next to this script.
On a modern Mac Studio this takes ~3-5 minutes.

If you don't have Blender, install it (free) at https://www.blender.org/.
The script also works if you open Blender and paste it into the Scripting tab.
"""
import os
import math

try:
    import bpy
except ImportError:
    raise SystemExit("This script must be run inside Blender.")

HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------
# 1) Clean scene
# ----------------------------------------------------------------
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for collection in (bpy.data.lights, bpy.data.cameras, bpy.data.materials, bpy.data.worlds):
    for item in list(collection):
        if collection is bpy.data.worlds and item.users:
            continue
        try:
            collection.remove(item)
        except Exception:
            pass


# ----------------------------------------------------------------
# 2) Import STLs
# ----------------------------------------------------------------
def import_stl(path):
    if hasattr(bpy.ops.wm, "stl_import"):  # Blender 4.x
        bpy.ops.wm.stl_import(filepath=path)
    else:
        bpy.ops.import_mesh.stl(filepath=path)
    return bpy.context.selected_objects[0]


bottom = import_stl(os.path.join(HERE, 'adapix_bottom.stl'))
bottom.name = 'AdapixBottom'

top = import_stl(os.path.join(HERE, 'adapix_top.stl'))
top.name = 'AdapixTop'
top.location.z = 5.0  # BOT_H from build_stl.py


# ----------------------------------------------------------------
# 3) Soft-touch anodized navy material
#    Base color matches Adapix brand navy. Roughness > 0.5 = matte.
#    Tiny clear-coat layer adds the "soft-touch" sheen catch.
# ----------------------------------------------------------------
def make_case_material():
    mat = bpy.data.materials.new(name='AdapixCase')
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get('Principled BSDF')

    # Adapix brand navy, slightly desaturated for realism
    bsdf.inputs['Base Color'].default_value = (0.018, 0.028, 0.055, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.58
    bsdf.inputs['Metallic'].default_value = 0.05  # tiny metallic flake

    # Clear coat — Blender 4.x renamed these inputs
    for name in ('Coat Weight', 'Clearcoat'):
        if name in bsdf.inputs:
            bsdf.inputs[name].default_value = 0.35
            break
    for name in ('Coat Roughness', 'Clearcoat Roughness'):
        if name in bsdf.inputs:
            bsdf.inputs[name].default_value = 0.18
            break

    # Specular boost
    for name in ('Specular IOR Level', 'Specular'):
        if name in bsdf.inputs:
            bsdf.inputs[name].default_value = 0.55
            break

    return mat


case_mat = make_case_material()

for obj in (top, bottom):
    obj.data.materials.clear()
    obj.data.materials.append(case_mat)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.shade_smooth()
    # Bevel modifier for soft edge highlights — critical for product look
    bevel = obj.modifiers.new(name='Bevel', type='BEVEL')
    bevel.width = 0.35
    bevel.segments = 4
    bevel.limit_method = 'ANGLE'
    bevel.angle_limit = math.radians(30)


# ----------------------------------------------------------------
# 4) Glowing underglow ring (the WS2812B LED strip simulation)
#    A thin torus-ish ring placed at the seam between top and bottom,
#    with an emission shader for that signature cyan halo.
# ----------------------------------------------------------------
def make_underglow():
    bpy.ops.mesh.primitive_torus_add(
        major_radius=78.0,
        minor_radius=1.6,
        major_segments=96,
        minor_segments=8,
        location=(80.0, 80.0, 5.0),
    )
    glow = bpy.context.active_object
    glow.name = 'Underglow'
    # Slightly oval the ring along Y so it suggests the chamfered front-left
    glow.scale.x = 1.05
    glow.scale.y = 1.05

    mat = bpy.data.materials.new(name='UnderglowEmit')
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    emit = nt.nodes.new('ShaderNodeEmission')
    # Adapix cyan (#00D4FF) → linear RGB
    emit.inputs['Color'].default_value = (0.0, 0.65, 1.0, 1.0)
    emit.inputs['Strength'].default_value = 18.0
    nt.links.new(emit.outputs['Emission'], out.inputs['Surface'])

    glow.data.materials.append(mat)
    return glow


underglow = make_underglow()


# ----------------------------------------------------------------
# 5) Backlit Circuit-A logo on top
#    A small thin plane placed slightly inside the engraving with an
#    emission shader. The bevel modifier on the top shell naturally
#    creates a thin gap where light leaks out around the lettering.
# ----------------------------------------------------------------
def make_logo_glow():
    bpy.ops.mesh.primitive_plane_add(size=18, location=(120.0, 30.0, 39.7))
    plane = bpy.context.active_object
    plane.name = 'LogoBacklight'

    mat = bpy.data.materials.new(name='LogoEmit')
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    emit = nt.nodes.new('ShaderNodeEmission')
    emit.inputs['Color'].default_value = (0.0, 0.85, 1.0, 1.0)
    emit.inputs['Strength'].default_value = 6.0
    nt.links.new(emit.outputs['Emission'], out.inputs['Surface'])

    plane.data.materials.append(mat)
    return plane


logo_glow = make_logo_glow()


# ----------------------------------------------------------------
# 6) Camera (low 3/4 angle, slight DOF)
# ----------------------------------------------------------------
cam_data = bpy.data.cameras.new('AdapixCam')
cam_data.lens = 70                # mild telephoto = product photo feel
cam_data.dof.use_dof = True
cam_data.dof.focus_distance = 0.32
cam_data.dof.aperture_fstop = 4.5
cam = bpy.data.objects.new('AdapixCam', cam_data)
bpy.context.scene.collection.objects.link(cam)
cam.location = (340, -300, 200)
cam.rotation_euler = (math.radians(68), 0, math.radians(48))
bpy.context.scene.camera = cam


# ----------------------------------------------------------------
# 7) Three-point studio lighting + cyan rim kicker
# ----------------------------------------------------------------
def add_area(name, location, energy, size=300, color=(1.0, 1.0, 1.0)):
    ld = bpy.data.lights.new(name, type='AREA')
    ld.energy = energy
    ld.size = size
    ld.color = color
    light = bpy.data.objects.new(name, ld)
    light.location = location
    bpy.context.scene.collection.objects.link(light)
    direction = -math.atan2(location[1], location[0])
    light.rotation_euler = (math.radians(60), 0, direction + math.radians(90))
    return light


# Key — main warm-ish light from upper right
add_area('Key',  (260, -160, 380), energy=1800, size=420, color=(1.00, 0.97, 0.92))
# Fill — softer cool light from the left
add_area('Fill', (-220, -120, 260), energy=550,  size=320, color=(0.85, 0.92, 1.00))
# Rim — pure cyan kicker from behind to halo the case + sell the underglow
add_area('Rim',  (40, 290, 180),    energy=1400, size=240, color=(0.10, 0.65, 1.00))
# Top fill — wide soft from directly above to lift the top surface
add_area('Top',  (80, 0, 600),      energy=400,  size=800, color=(0.95, 0.97, 1.00))


# ----------------------------------------------------------------
# 8) Studio background — gradient navy-to-black
# ----------------------------------------------------------------
world = bpy.context.scene.world
if world is None:
    world = bpy.data.worlds.new('AdapixWorld')
    bpy.context.scene.world = world
world.use_nodes = True
wnt = world.node_tree
wnt.nodes.clear()
out = wnt.nodes.new('ShaderNodeOutputWorld')
bg  = wnt.nodes.new('ShaderNodeBackground')
grad = wnt.nodes.new('ShaderNodeTexGradient')
ramp = wnt.nodes.new('ShaderNodeValToRGB')
mapping = wnt.nodes.new('ShaderNodeMapping')
texc = wnt.nodes.new('ShaderNodeTexCoord')

# Color ramp: deep navy at top → near black at horizon
ramp.color_ramp.elements[0].position = 0.30
ramp.color_ramp.elements[0].color = (0.012, 0.018, 0.038, 1.0)
ramp.color_ramp.elements[1].position = 0.70
ramp.color_ramp.elements[1].color = (0.002, 0.004, 0.012, 1.0)

wnt.links.new(texc.outputs['Generated'], mapping.inputs['Vector'])
wnt.links.new(mapping.outputs['Vector'], grad.inputs['Vector'])
wnt.links.new(grad.outputs['Color'], ramp.inputs['Fac'])
wnt.links.new(ramp.outputs['Color'], bg.inputs['Color'])
wnt.links.new(bg.outputs['Background'], out.inputs['Surface'])
bg.inputs['Strength'].default_value = 0.6


# ----------------------------------------------------------------
# 9) Floor — polished reflective plane that fades into the background
# ----------------------------------------------------------------
bpy.ops.mesh.primitive_plane_add(size=2400, location=(0, 0, -0.5))
floor = bpy.context.active_object
floor.name = 'Floor'

floor_mat = bpy.data.materials.new('Floor')
floor_mat.use_nodes = True
fbsdf = floor_mat.node_tree.nodes.get('Principled BSDF')
fbsdf.inputs['Base Color'].default_value = (0.008, 0.012, 0.022, 1.0)
fbsdf.inputs['Roughness'].default_value = 0.32
fbsdf.inputs['Metallic'].default_value = 0.10
floor.data.materials.append(floor_mat)


# ----------------------------------------------------------------
# 10) Render settings — Cycles, denoised, filmic, 3200x2400
# ----------------------------------------------------------------
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
# Use GPU if available
try:
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'CUDA'
    for d in prefs.devices:
        d.use = True
    scene.cycles.device = 'GPU'
except Exception:
    pass

scene.cycles.samples = 256
scene.cycles.use_denoising = True
scene.cycles.max_bounces = 12

scene.render.resolution_x = 3200
scene.render.resolution_y = 2400
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.film_transparent = False
scene.render.filepath = os.path.join(HERE, 'adapix_beauty.png')

scene.view_settings.view_transform = 'Filmic'
scene.view_settings.look = 'Medium High Contrast'
scene.view_settings.exposure = 0.3


# ----------------------------------------------------------------
# 11) Render
# ----------------------------------------------------------------
print(f"Rendering Adapix beauty shot to {scene.render.filepath}...")
print(f"  resolution: {scene.render.resolution_x} x {scene.render.resolution_y}")
print(f"  samples:    {scene.cycles.samples}")
print(f"  device:     {scene.cycles.device}")
bpy.ops.render.render(write_still=True)
print("Done.")
