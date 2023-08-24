#--------------------------------------------------------------
# Meta Dictionary
#--------------------------------------------------------------

bl_info = {
	"name" : "MattePainter",
	"author" : "SceneFiller",
	"version" : (1, 0, 6),
	"blender" : (3, 3, 0),
	"location" : "View3d > Tool",
	"warning" : "",
	"wiki_url" : "",
	"category" : "3D View",
}

#--------------------------------------------------------------
# Import
#--------------------------------------------------------------

import os
import bpy
import bpy_extras
from bpy.props import PointerProperty
import math 
from mathutils import Vector
import mathutils
from bpy_extras.image_utils import load_image
from pathlib import Path
import shutil
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ImportHelper
from PIL import Image
import time, sys

# Draw Functions
import blf
import gpu
from gpu_extras.batch import batch_for_shader


#--------------------------------------------------------------
# Miscellaneous Functions
#--------------------------------------------------------------

def MATTEPAINTER_FN_findLayerCollectionByName(name, collection):
	# Recursive search for a Collection with the name "MattePainter".
	for c in collection.children:
		if c.name == name:
			return c
	return None

def MATTEPAINTER_FN_createMattePainterCollection():
	# Creates a MattePainter collection if it doesn't already exist.
	# Also sets the existing or newly created MattePainter collection to Active.
	collection = MATTEPAINTER_FN_findLayerCollectionByName("MattePainter", bpy.context.view_layer.layer_collection)
	if collection:
		bpy.context.view_layer.active_layer_collection = collection
	else:
		new_collection = bpy.data.collections.new("MattePainter")
		bpy.context.scene.collection.children.link(new_collection)
		collection = MATTEPAINTER_FN_findLayerCollectionByName("MattePainter", bpy.context.view_layer.layer_collection)
		bpy.context.view_layer.active_layer_collection = collection

def MATTEPAINTER_FN_setDimensions(target, image, camera, scene):
	# Correctly adjusts the Aspect Ratio of the Plane to match the Image Dimensions & rotates it to face the Camera.
	view_frame = camera.data.view_frame(scene=scene)
	frame_size = Vector([max(v[i] for v in view_frame) for i in range(3)]) - Vector([min(v[i] for v in view_frame) for i in range(3)])
	camera_aspect = frame_size.x / frame_size.y

	if camera.type == 'ORTHO':
	    frame_size = frame_size.xy
	else:
	    distance = bpy_extras.object_utils.world_to_camera_view(scene, camera, scene.cursor.location).z
	    frame_size = distance * frame_size.xy / (-view_frame[0].z)

	if image.size[0] > image.size[1]:
	    ratio = image.size[1] / image.size[0]
	    target.scale = (1.0, ratio, 1.0)
	else:
	    ratio = image.size[0] / image.size[1]
	    target.scale = (ratio, 1.0, 1.0)

def MATTEPAINTER_FN_addMask(name, width, height):
	mask = bpy.data.images.new(name=name, width=width, height=height)
	pixels = [1.0] * (4 * width * height)

	mask.pixels = pixels
	return mask 

def MATTEPAINTER_FN_setShaders(nodes, links, image_file, mask=None, isPaintLayer=False):
	material_output = nodes.get("Material Output") # Output Node
	principled_bsdf = nodes.get("Principled BSDF") 
	nodes.remove(principled_bsdf) # Delete BSDF

	node_emission = nodes.new(type="ShaderNodeEmission")
	node_transparent = nodes.new(type="ShaderNodeBsdfTransparent")
	node_mix = nodes.new(type="ShaderNodeMixShader")
	node_invert = nodes.new(type="ShaderNodeInvert")
	node_opacity = nodes.new(type="ShaderNodeMixRGB")
	node_curves = nodes.new(type="ShaderNodeRGBCurve")
	node_HSV = nodes.new(type="ShaderNodeHueSaturation")
	node_noise = nodes.new(type="ShaderNodeTexNoise")
	node_mixRGB = nodes.new(type="ShaderNodeMixRGB")
	node_overlayRGB = nodes.new(type="ShaderNodeMixRGB")
	node_coord = nodes.new(type="ShaderNodeTexCoord")
	node_albedo = nodes.new(type="ShaderNodeTexImage")
		
	# Naming Nodes for Color Grading
	node_overlayRGB.name = 'blur_mix'
	node_curves.name = 'curves'
	node_HSV.name = 'HSV'	
	node_opacity.name = 'opacity'	
	node_albedo.name = 'albedo'
	node_mix.name = 'mix'
	node_invert.name = 'invert'

	# Default Values
	node_invert.mute = True
	node_albedo.image = image_file

	if image_file.source == "MOVIE":
		node_albedo.image_user.use_cyclic = True 
		node_albedo.image_user.use_auto_refresh = True
		node_albedo.image_user.frame_duration = image_file.frame_duration		

	# Setup Mask
	if not mask == None:
		node_mask = nodes.new(type="ShaderNodeTexImage")	
		node_mask.name = 'transparency_mask'		
		node_mask.image = mask
		node_mask.select = True		
		nodes.active = node_mask	

	node_noise.inputs[2].default_value = 1000000.0
	node_opacity.inputs[0].default_value = 1.0
	node_mixRGB.blend_type = "MIX"
	node_mixRGB.inputs[0].default_value = 0.0
	node_overlayRGB.blend_type = "OVERLAY"
	node_overlayRGB.inputs[0].default_value = 0.0
	node_opacity.inputs[0].default_value = 1.0
	node_opacity.inputs[1].default_value = (0, 0, 0, 1)

	# Connections
	link = links.new(node_albedo.outputs[0], node_curves.inputs[1]) # Albedo -> Curves
	link = links.new(node_curves.outputs[0], node_HSV.inputs[4]) # Curves -> HSV
	link = links.new(node_HSV.outputs[0], node_emission.inputs[0]) # Curves -> Emission
	link = links.new(node_emission.outputs[0], node_mix.inputs[2]) # Emission -> Mix Shader	
	link = links.new(node_transparent.outputs[0], node_mix.inputs[1]) # Transparent BSDF -> Mix Shader
	link = links.new(node_invert.outputs[0], node_opacity.inputs[2]) # Invert -> Opacity
	link = links.new(node_opacity.outputs[0], node_mix.inputs[0]) # Opacity -> Mix
	link = links.new(node_mix.outputs[0], material_output.inputs[0]) # Mix -> Output
	link = links.new(node_coord.outputs[2], node_mixRGB.inputs[1]) # Coord -> MixRGB
	link = links.new(node_coord.outputs[2], node_noise.inputs[0]) # Coord -> Noise
	link = links.new(node_noise.outputs[1], node_overlayRGB.inputs[2]) # Noise -> OverlayRGB
	link = links.new(node_mixRGB.outputs[0], node_overlayRGB.inputs[1]) # MixRGB -> OverlayRGB
	link = links.new(node_overlayRGB.outputs[0], node_albedo.inputs[0]) # OverlayRGB -> Albedo

	if not mask == None:
		link = links.new(node_mask.outputs[0], node_invert.inputs[1]) # Mask -> Invert Input
		link = links.new(node_overlayRGB.outputs[0], node_mask.inputs[0]) # OverlayRGB -> Mask
	else:
		link = links.new(node_albedo.outputs[1], node_invert.inputs[1]) # Albedo Alpha -> Invert Input
		link = links.new(node_overlayRGB.outputs[0], node_albedo.inputs[0]) # OverlayRGB -> Albedo

	# Node Positions
	material_output.location = Vector((100.0, 0.0))
	node_mix.location = Vector((-100.0, 0.0))
	node_emission.location = Vector((-300.0, -200.0))
	node_transparent.location= Vector((-300.0, -50.0))
	node_albedo.location = Vector((-1100.0, -300.0))	
	node_invert.location = Vector((-800.0, 200.0))
	node_opacity.location = Vector((-500.0, 200.0))
	node_HSV.location = Vector((-500.0, -300.0))
	node_curves.location = Vector((-800.0, -300.0))
	node_overlayRGB.location = Vector((-1400.0, 0.0))
	node_mixRGB.location = Vector((-1600.0, 200.0))
	node_noise.location = Vector((-1600.0, -200.0))
	node_coord.location = Vector((-1800.0, 0.0))	
	if not mask == None:
		node_mask.location = Vector((-1100.0, 200.0))


def MATTEPAINTER_FN_rayCast(raycast_data):
	mouse_x, context_x, mouse_y, context_y = raycast_data
	mouse_position = Vector(((mouse_x) - context_x, mouse_y - context_y))
	region = bpy.context.region 
	region_data = bpy.context.region_data
	ray_vector = view3d_utils.region_2d_to_vector_3d(region, region_data, mouse_position)
	ray_origin = view3d_utils.region_2d_to_origin_3d(region, region_data, mouse_position)
	direction = ray_origin + (ray_vector * 1000)
	direction -= ray_origin
	result, location, normal, index, obj, matrix = bpy.context.scene.ray_cast(bpy.context.view_layer.depsgraph, ray_origin, direction)

	return result, location, normal, index, obj, matrix

def MATTEPAINTER_FN_convertToStroke(name, is_start=False, mouse=(0,0), brush_size=1, time=0.0):
	stroke = {"name": name,
				"is_start": is_start,
				"location": (0,0,0),
				"mouse":mouse,
				"mouse_event":(0,0),
				"pen_flip":False,
				"pressure":1.0,
				"size":brush_size,
				"time":time,
				"x_tilt":0.0,
				"y_tilt":0.0}
	return stroke

def MATTEPAINTER_FN_contextOverride(area_to_check):
	return [area for area in bpy.context.screen.areas if area.type == area_to_check][0]

def get_override(area_type, region_type):
	for area in bpy.context.screen.areas: 
		if area.type == area_type:             
			for region in area.regions:                 
				if region.type == region_type:                    
					override = {'area': area, 'region': region} 
					return override

def MATTEPAINTER_FN_useImageEditor(use_image_editor=True):
	for area in bpy.context.screen.areas:
		if use_image_editor and area.type=='VIEW_3D':
			area.type = 'IMAGE_EDITOR'
			bpy.context.space_data.ui_mode = 'PAINT'
		elif use_image_editor==False and area.type=='IMAGE_EDITOR':
			area.type = 'VIEW_3D'	

def MATTEPAINTER_FN_get2DCoords(self, context):
	# get vertex 0 (bottom left)
	# get vertex 2 (i think 2, top right)
	# calculate screen-space area 
	# if in bounds (marquee start)
		# 
	return (0, 0)

def MATTEPAINTER_FN_shrinkSelection(self, context):
	# need to grab the min X, max X, min Y, max Y of selection
	# calculate the mid point
	# then run a for loop
	# if x is less than midpoint
		# move point of curve 1 x
	# if x is more than midpoint
		# move point of curve -1 x 
	# if x == midpoint
		# do nothing
	# repeat for Y
	# this function needs to be called AFTER the paint bucket fill is done
	return 

def MATTEPAINTER_FN_drawMarqueeCallback(self, context):
	if self.mouse_down:
		start_vert, end_vert = self.mouse_positions

		corner_vert_a = (self.mouse_positions[0][0], self.mouse_positions[1][1])
		corner_vert_b = (self.mouse_positions[1][0], self.mouse_positions[0][1])
		verts = (start_vert, corner_vert_a, corner_vert_b, end_vert)
		indices = ((0, 1, 2), (1, 2, 3))
		shader = gpu.shader.from_builtin('UNIFORM_COLOR')
		gpu.state.blend_set('ALPHA')
		gpu.state.line_width_set(2.0)
		batch = batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=indices)
		shader.uniform_float("color", (0.0, 0.0, 0.0, 0.4))
		batch.draw(shader)	
		gpu.state.line_width_set(1.0)
		gpu.state.blend_set('NONE')   	

def MATTEPAINTER_FN_drawLassoCallback(self, context):
	# This draws pixels onto the screen directly, used for Lasso and Marquee selection
	shader = gpu.shader.from_builtin('UNIFORM_COLOR')
	gpu.state.blend_set('ALPHA')
	gpu.state.line_width_set(2.0)
	batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": self.mouse_path})
	shader.uniform_float("color", (0.0, 0.0, 0.0, 0.7))
	batch.draw(shader)	
	gpu.state.line_width_set(1.0)
	gpu.state.blend_set('NONE')

#--------------------------------------------------------------
# Layer Creation
#--------------------------------------------------------------		
		

class MATTEPAINTER_OT_newLayerFromFile(bpy.types.Operator, ImportHelper):
	# Utilizes ImportHelper to open a File Browser and load an Image File.
	# Creates a Plane object and orients it correctly, then builds Shader Tree.
	bl_idname = "mattepainter.new_layer_from_file"
	bl_label = "Import image file."
	bl_description = "Imports an image file and automatically builds the Shader Tree"
	bl_options = {"REGISTER"}

	filter_glob: bpy.props.StringProperty(
			default='*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.bmp;*.avi;*.mp4;*.mov;*.webm;*.mkv;',
			options={'HIDDEN'}
		)

	def execute(self, context):
		# Camera Safety Check
		camera = bpy.context.scene.camera
		if not camera: # Safety Check
			bpy.ops.object.camera_add(enter_editmode=False, align='VIEW', location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1))
		camera = bpy.context.scene.camera

		# Create Collection
		MATTEPAINTER_FN_createMattePainterCollection()	

		# Image Loading
		image = load_image(self.filepath, check_existing=True)

		# Mask Generation
		mask_name = "mask_" + image.name
		mask = MATTEPAINTER_FN_addMask(name=mask_name, width=image.size[0], height=image.size[1])		

		# Geometry and Alignment
		bpy.ops.mesh.primitive_plane_add(enter_editmode=False, align='WORLD', location=(0, 0, 0), scale=(1, 1, 1))
		bpy.ops.object.mode_set(mode="EDIT")
		bpy.ops.mesh.subdivide(number_cuts=1)
		bpy.ops.object.mode_set(mode="OBJECT")

		active_object = bpy.context.active_object
		active_object.name = image.name
		scene = bpy.context.scene

		active_object.rotation_euler = camera.rotation_euler
		MATTEPAINTER_FN_setDimensions(target=active_object, image=image, camera=camera, scene=scene)
		bpy.ops.object.transform_apply(scale=True)

		# Shader Setup
		material = bpy.data.materials.new(name=image.name)
		active_object.data.materials.append(material)
		material.blend_method = "BLEND"
		material.shadow_method = "CLIP"
		material.use_nodes = True
		nodes = material.node_tree.nodes
		links = material.node_tree.links

		MATTEPAINTER_FN_setShaders(nodes=nodes, links=links, image_file=image, mask=mask, isPaintLayer=False)	

		# End Method
		return {'FINISHED'}	

class MATTEPAINTER_OT_newEmptyPaintLayer(bpy.types.Operator):
	bl_idname = "mattepainter.new_empty_paint_layer"
	bl_label = "Creates a new empty layer for painting."
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Creates a new empty layer for painting"

	def execute(self, context):		
		# Camera Safety Check
		camera = bpy.context.scene.camera
		if not camera: # Safety Check
			bpy.ops.object.camera_add(enter_editmode=False, align='VIEW', location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1))
		camera = bpy.context.scene.camera

		# Create Collection
		MATTEPAINTER_FN_createMattePainterCollection()	

		# Image Generation
		render = bpy.data.scenes[0].render
		width = render.resolution_x
		height = render.resolution_y
		image = bpy.data.images.new(name="PaintLayer", width=width, height=height)
		pixels = [0.0] * (4 * width * height)
		image.pixels = pixels

		# Geometry and Alignment
		bpy.ops.mesh.primitive_plane_add(enter_editmode=False, align='WORLD', location=(0, 0, 0), scale=(1, 1, 1))
		bpy.ops.object.mode_set(mode="EDIT")
		bpy.ops.mesh.subdivide(number_cuts=1)
		bpy.ops.object.mode_set(mode="OBJECT")
		
		active_object = bpy.context.active_object
		active_object.name = image.name
		scene = bpy.context.scene

		active_object.rotation_euler = camera.rotation_euler
		MATTEPAINTER_FN_setDimensions(target=active_object, image=image, camera=camera, scene=scene)
		bpy.ops.object.transform_apply(scale=True)

		# Shader Setup
		material = bpy.data.materials.new(name=image.name)
		active_object.data.materials.append(material)
		material.blend_method = "BLEND"
		material.shadow_method = "CLIP"
		material.use_nodes = True
		nodes = material.node_tree.nodes
		links = material.node_tree.links

		MATTEPAINTER_FN_setShaders(nodes=nodes, links=links, image_file=image, mask=None, isPaintLayer=True)

		return {'FINISHED'}

class MATTEPAINTER_OT_newLayerFromClipboard(bpy.types.Operator):
	bl_idname = "mattepainter.new_layer_from_clipboard"
	bl_label = "Imports an image directly from the Clipboard."
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Imports an image directly from the Clipboard"

	def execute(self, context):
		camera = bpy.context.scene.camera
		if not camera: # Safety Check
			bpy.ops.object.camera_add(enter_editmode=False, align='VIEW', location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1))
		camera = bpy.context.scene.camera

		# Create Collection
		MATTEPAINTER_FN_createMattePainterCollection()	

		# Paste Image
		for window in context.window_manager.windows:
		    screen = window.screen
		    for area in screen.areas:
		        if area.type == 'VIEW_3D':
		        	area.type = 'IMAGE_EDITOR'
		        	try:
		        		bpy.ops.image.clipboard_paste()
		        	except:
		        		area.type='VIEW_3D'
		        		return{'CANCELLED'}
		        	else:
		        		image = area.spaces.active.image   
		        		area.type='VIEW_3D'   
		        	break

		# Mask Generation
		mask_name = "mask_" + image.name
		mask = MATTEPAINTER_FN_addMask(name=mask_name, width=image.size[0], height=image.size[1])		

		# Geometry and Alignment
		bpy.ops.mesh.primitive_plane_add(enter_editmode=False, align='WORLD', location=(0, 0, 0), scale=(1, 1, 1))
		bpy.ops.object.mode_set(mode="EDIT")
		bpy.ops.mesh.subdivide(number_cuts=1)
		bpy.ops.object.mode_set(mode="OBJECT")

		active_object = bpy.context.active_object
		active_object.name = image.name
		scene = bpy.context.scene

		active_object.rotation_euler = camera.rotation_euler
		MATTEPAINTER_FN_setDimensions(target=active_object, image=image, camera=camera, scene=scene)
		bpy.ops.object.transform_apply(scale=True)

		# Shader Setup
		material = bpy.data.materials.new(name=image.name)
		active_object.data.materials.append(material)
		material.blend_method = "BLEND"
		material.shadow_method = "CLIP"
		material.use_nodes = True
		nodes = material.node_tree.nodes
		links = material.node_tree.links

		MATTEPAINTER_FN_setShaders(nodes=nodes, links=links, image_file=image, mask=mask, isPaintLayer=False)	
		self.report({"INFO"}, "Imported Clipboard.")	
		return {'FINISHED'}		

#--------------------------------------------------------------
# Layer Functions
#--------------------------------------------------------------		

class MATTEPAINTER_OT_paintMask(bpy.types.Operator):
	# Switches to Texture Paint Mode.
	bl_idname = "mattepainter.paint_mask"
	bl_label = "Switch to Mask Paint mode."
	bl_description = "Switch to Mask Paint mode"
	bl_options = {"REGISTER", "UNDO"}

	def execute(self, context):
		# Safety Checks
		if len(context.selected_objects) == 0:	
			return {'CANCELLED'}		
		if not context.active_object.type == "MESH": 
			self.report({"WARNING"}, "Target Object is not Paintable.")	
			return {'CANCELLED'}
		bpy.ops.object.transform_apply(scale=True)
		bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
		bpy.context.scene.tool_settings.image_paint.use_backface_culling = False
		return {'FINISHED'}

class MATTEPAINTER_OT_layerSelect(bpy.types.Operator):
	# Selects the indexed Object via the Layers panel.
	bl_idname = "mattepainter.layer_select"
	bl_label = "Select layer."
	bl_description = "Selects the Layer"
	bl_options = {"REGISTER", "UNDO"}
	MATTEPAINTER_VAR_layerIndex: bpy.props.IntProperty(name='MATTEPAINTER_VAR_layerIndex', description='',subtype='NONE', options={'HIDDEN'}, default=0)

	def execute(self, context):
		objects = bpy.data.collections[r"MattePainter"].objects

		for obj in bpy.context.selected_objects:
			obj.select_set(False)

		if len(objects) > 0:
			if context.mode in ['PAINT_TEXTURE'] and objects[self.MATTEPAINTER_VAR_layerIndex].type == 'MESH':	
				bpy.ops.object.mode_set(mode='OBJECT')
				objects[self.MATTEPAINTER_VAR_layerIndex].select_set(True)
				bpy.context.view_layer.objects.active = objects[self.MATTEPAINTER_VAR_layerIndex]							
				bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
			else:
				objects[self.MATTEPAINTER_VAR_layerIndex].select_set(True)
				bpy.context.view_layer.objects.active = objects[self.MATTEPAINTER_VAR_layerIndex]	

		return {'FINISHED'}

class MATTEPAINTER_OT_layerVisibility(bpy.types.Operator):
	# Toggles visibility for the Layer.
	bl_idname = "mattepainter.layer_visibility"
	bl_label = "Toggle Layer Visibility."
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Hides/Shows the Layer from both Viewport & Renders"
	MATTEPAINTER_VAR_layerIndex: bpy.props.IntProperty(name='MATTEPAINTER_VAR_layerIndex', description='',subtype='NONE', options={'HIDDEN'}, default=0)

	def execute(self, context):

		objects = bpy.data.collections[r"MattePainter"].objects 
		objects[self.MATTEPAINTER_VAR_layerIndex].hide_viewport = 1-objects[self.MATTEPAINTER_VAR_layerIndex].hide_render
		objects[self.MATTEPAINTER_VAR_layerIndex].hide_render = 1-objects[self.MATTEPAINTER_VAR_layerIndex].hide_render
		return {'FINISHED'}

class MATTEPAINTER_OT_layerLock(bpy.types.Operator):
	# Toggles selection for the Layer.
	bl_idname = "mattepainter.layer_lock"
	bl_label = "Toggle Layer Selection."
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Locks the Layer"
	MATTEPAINTER_VAR_layerIndex: bpy.props.IntProperty(name='MATTEPAINTER_VAR_layerIndex', description='',subtype='NONE', options={'HIDDEN'}, default=0)

	def execute(self, context):
		objects = bpy.data.collections[r"MattePainter"].objects 
		objects[self.MATTEPAINTER_VAR_layerIndex].hide_select = 1-objects[self.MATTEPAINTER_VAR_layerIndex].hide_select
		return {'FINISHED'}

class MATTEPAINTER_OT_layerInvertMask(bpy.types.Operator):
	bl_idname = "mattepainter.invert_mask"
	bl_label = "Toggle Mask Inversion"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Toggles mask inversion for the Layer"
	MATTEPAINTER_VAR_layerIndex: bpy.props.IntProperty(name='MATTEPAINTER_VAR_layerIndex', description='',subtype='NONE', options={'HIDDEN'}, default=0)

	def execute(self, context):
		objects = bpy.data.collections[r"MattePainter"].objects 

		material = objects[self.MATTEPAINTER_VAR_layerIndex].data.materials[0]
		nodes = material.node_tree.nodes
		node_mask = nodes.get('invert')
		node_mask.mute = 1-node_mask.mute
		return {'FINISHED'}	

class MATTEPAINTER_OT_layerInvertMaskActive(bpy.types.Operator):
	bl_idname = "mattepainter.invert_mask_active"
	bl_label = "Invert Mask (Active)"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Toggles mask inversion for the Active Layer"
	MATTEPAINTER_VAR_layerIndex: bpy.props.IntProperty(name='MATTEPAINTER_VAR_layerIndex', description='',subtype='NONE', options={'HIDDEN'}, default=0)

	def execute(self, context):
		active_object = bpy.context.active_object
	
		material = active_object.data.materials[0]
		nodes = material.node_tree.nodes
		node_mask = nodes.get('invert')
		node_mask.mute = 1-node_mask.mute
		return {'FINISHED'}	


class MATTEPAINTER_OT_layerShowMask(bpy.types.Operator):
	bl_idname = "mattepainter.show_mask"
	bl_label = "Displays Transparency Mask"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Toggles displaying the Transparency Mask for the Layer"
	MATTEPAINTER_VAR_layerIndex: bpy.props.IntProperty(name='MATTEPAINTER_VAR_layerIndex', description='',subtype='NONE', options={'HIDDEN'}, default=0)

	def execute(self, context):
		objects = bpy.data.collections[r"MattePainter"].objects 

		material = objects[self.MATTEPAINTER_VAR_layerIndex].data.materials[0]
		nodes = material.node_tree.nodes
		links = material.node_tree.links

		mask = nodes.get("transparency_mask")
		albedo = nodes.get("albedo")
		curves = nodes.get("curves")
		opacity = nodes.get("opacity")
		mix = nodes.get("mix")
		invert = nodes.get("invert")
		
		if mask.outputs[0].links[0].to_node.name == "invert":
			links.remove(mask.outputs[0].links[0])
			links.remove(albedo.outputs[0].links[0])
			links.remove(opacity.outputs[0].links[0])
			mix.inputs[0].default_value = 1.0
			link = links.new(mask.outputs[0], curves.inputs[1])
		else:
			links.remove(mask.outputs[0].links[0])
			link = links.new(mask.outputs[0], invert.inputs[1])
			link = links.new(albedo.outputs[0], curves.inputs[1])
			link = links.new(opacity.outputs[0], mix.inputs[0])
		
		return {'FINISHED'}			

class MATTEPAINTER_OT_makeUnique(bpy.types.Operator):
	# Makes a duplicated Object unique.
	bl_idname = "mattepainter.make_unique"
	bl_label = "Creates a unique Shader Tree for a duplicated Object."
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Creates a unique Shader Tree for a duplicated Object"

	def execute(self, context):
		# check if active object is inside MattePainter
		# if True, create new shader tree for it
		active_object = bpy.context.active_object
		if active_object.users_collection[0] == bpy.data.collections['MattePainter']:			
			new_material = active_object.data.materials[0].copy()
			active_object.data.materials[0] = new_material
			material = active_object.data.materials[0]
			nodes = material.node_tree.nodes 
			image = nodes.get('albedo').image
			width = image.size[0]		
			height = image.size[1]
			node_mask = nodes.get('transparency_mask')
			new_mask = bpy.data.images.new(name=(r"mask_" + active_object.name), width=width, height=height, alpha=True, float_buffer=False, stereo3d=False, is_data=False, tiled=False, )
			pixels = [1.0] * (4 * width * height)
			new_mask.pixels = pixels
			node_mask.image = new_mask

		return {'FINISHED'}		

#--------------------------------------------------------------
# File Management Functions
#--------------------------------------------------------------		

class MATTEPAINTER_OT_moveToCamera(bpy.types.Operator):
	# Moves the plane in front of the camera and re-aligns it.
	bl_idname = "mattepainter.move_to_camera"
	bl_label = "Moves the plane in front of the camera and re-aligns it."
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Moves the plane in front of the camera and re-aligns it"

	def execute(self, context):
		active_object = bpy.context.active_object
		if active_object.users_collection[0] == bpy.data.collections['MattePainter']:
			camera = bpy.context.scene.camera
			
			focal_length = camera.data.lens 
			distance_per_mm = 0.0452
			limit_distance = distance_per_mm * focal_length

			constraint = active_object.constraints.new(type="LIMIT_DISTANCE")
			constraint.target = camera
			constraint.distance = limit_distance 
			bpy.ops.constraint.apply(constraint=constraint.name)

		return {'FINISHED'}	

class MATTEPAINTER_OT_makeSequence(bpy.types.Operator):
	# Converts an imported image into a Sequence.
	bl_idname = "mattepainter.make_sequence"
	bl_label = "Converts an imported image into a Sequence"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Converts an imported image into a Sequence"

	def execute(self, context):
		# check if active object is inside MattePainter
		# if True, create new shader tree for it
		active_object = bpy.context.active_object
		if active_object.users_collection[0] == bpy.data.collections['MattePainter']:			
			material = active_object.data.materials[0]
			nodes = material.node_tree.nodes 
			image = nodes.get('albedo').image
			image.source = 'SEQUENCE'
			image_user = nodes.get('albedo').image_user
			image_user.use_cyclic = True 
			image_user.use_auto_refresh = True

			for root, dirs, files in os.walk(Path(image.filepath).parent.absolute()):
				files = [file for file in files if file.endswith(('.jpg', '.jpeg', '.png', '.tif', 'tiff'))]
				frames = len(files)

			if frames > 1 and not frames == None:
				image_user.frame_duration = frames
			else:
				image_user.frame_duration = 1

		return {'FINISHED'}	

class MATTEPAINTER_OT_saveAllImages(bpy.types.Operator):
	# Saves all edited Image files.
	bl_idname = "mattepainter.save_all_images"
	bl_label = "Saves all modified Images."
	bl_description = "Saves all modified images"
	bl_options = {"REGISTER"}

	def execute(self, context):
		try:
			bpy.ops.image.save_all_modified()
			self.report({"INFO"}, "Images saved successfully.")
		except:
			self.report({"WARNING"}, "Images unchanged, no save necessary.")
			return {'CANCELLED'}
		return {'FINISHED'}

class MATTEPAINTER_OT_clearUnused(bpy.types.Operator):
	# Purges unused Data Blocks.
	bl_idname = "mattepainter.clear_unused"
	bl_label = "Purges unused Data Blocks."
	bl_description = "Removes unlinked data from the Blend File. WARNING: This process cannot be undone"
	bl_options = {"REGISTER"}

	def execute(self, context):
		bpy.ops.outliner.orphans_purge('INVOKE_DEFAULT' if True else 'EXEC_DEFAULT', num_deleted=0, do_local_ids=True, do_linked_ids=False, do_recursive=True)
		#self.report({"INFO"}, "Purged unused data.")
		return {'FINISHED'}

class MATTEPAINTER_OT_bakeProjection(bpy.types.Operator):
	# Bakes the Emit information from a texture into a new Image.
	bl_idname = "mattepainter.clear_unused"
	bl_label = "Purges unused Data Blocks."
	bl_description = "Bakes a Window-Based UV projection into a new Texture for 3D Projections."
	bl_options = {"REGISTER", "UNDO"}


	def execute(self, context):
		# IMPORTANT need to add a safety check to make sure we have an empty image selected
		# IMPORTANT also need a safety check to make sure we're in camera view
		# smart UV project object
		# get node tree
		# create an image tex node
		# create an image file
			# set the image resolution to current image texture (emit input)
		# if in eevee, switch to Cycles
		# set TextureCoord to Window, set Repeat to Clip in Image
		# jump to CAMERA VIEW
		# bake texture
		# return to previous view, return to previous Render Engine
		# 
		return{'FINISHED'}

#--------------------------------------------------------------
# ____NOT_IMPLEMENTED
#--------------------------------------------------------------

class MATTEPAINTER_OT_toolBrush(bpy.types.Operator):
	bl_idname = "mattepainter.tool_brush"
	bl_label = "Brush Tool"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Switches draw type to Brush"

	@classmethod
	def poll(cls, context):
		return context.mode in ['PAINT_TEXTURE']

	def execute(self, context):
		bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
		bpy.data.brushes["TexDraw"].stroke_method = 'SPACE'
		return {'FINISHED'}		

class MATTEPAINTER_OT_toolLine(bpy.types.Operator):
	bl_idname = "mattepainter.tool_line"
	bl_label = "Line Tool"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Switches draw type to Line"

	@classmethod
	def poll(cls, context):
		return context.mode in ['PAINT_TEXTURE']

	def execute(self, context):
		bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
		bpy.data.brushes["TexDraw"].stroke_method = 'LINE'
		return {'FINISHED'}

class MATTEPAINTER_OT_selectionMarquee(bpy.types.Operator):

	# https://www.youtube.com/watch?v=YRwToPPGaZU WATCH THIS 100 TIMES
	# TLDR: fill tool works in 2d
	# bpy.ops.paintcurve.select(location=(232, 395))

	# blender's internal 2d paint fill is called: paint_2d_bucket_fill
	# https://blender.stackexchange.com/questions/46467/incorrect-context-bpy-ops-paint-image-paintmode-normal-stroke-none

	bl_idname = "mattepainter.select_marquee"
	bl_label = "Marquee Fill"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Selects pixels using a Marquee-style selection"

	mouse_down = False

	def _set_pixel(x, y, width, colour):
		offset = (x + int(y*width)) * 4
		for i in range(4):
			image.pixels[offset+i] = colour

	def _in_bounds(self, check_position, top_left_corner, bottom_right_corner):
		mouse_x, mouse_y = check_position
		min_x, max_y = top_left_corner
		max_x, min_y = bottom_right_corner
		if mouse_x > min_x and mouse_x < max_x and mouse_y > min_y and mouse_y < max_y:
			return True 
		else:
			return False

	def _calculate_screen_space_offset(self, position, x):
		offset_x = start_position[0] - top_left_2d[0]
		offset_x_percentage = (offset_x / scale_2d[0])
		return

	def _fill_marquee(self):
		'''
		OKAY HERE ARE THE WORKING STEPS:

		**** there might be a better way to handle resolution, if I can copy the exact Framing/Zoom of the 3D View into the Image Editor

		1. after marquee curve is Painted, remove all but 2 points (need to have 2)
		2. Move those 2 points into the MIDDLE of the marquee
		3. switch to fill tool
		4. set fill tool Stroke to Curve
		5. load the same Curve that we just made
		6. use bpy.ops.paintcurve.draw()
		7. revert tools, delete curve etc etc cleanup etc

		'''

		bpy.ops.wm.tool_set_by_id(name="builtin_brush.Fill")
		stroke = [{"name": "", "is_start": True, "location": (0,0,0), "mouse":(0,0), "mouse_event":(0,0), "pen_flip":False, "pressure":1.0, "size":3, "time":0, "x_tilt":0.0, "y_tilt":0.0}]
		bpy.context.window.cursor_warp(400, 400)
		bpy.ops.paint.image_paint()
	

	def _draw_marquee(self):
		# Frame selected in 3D_VIEW
		bpy.ops.view3d.view_selected(use_all_regions=False)
		previous_stroke_method = bpy.context.tool_settings.image_paint.brush.stroke_method
		previous_falloff = bpy.context.tool_settings.image_paint.brush.curve_preset

		bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
		bpy.context.tool_settings.image_paint.brush.stroke_method = 'CURVE'

		bpy.ops.paintcurve.new()		
		paint_curve = bpy.data.paint_curves[-1]

		paint_curve.name = "MATTEPAINTER_MarqueeCurve"

		bpy.context.tool_settings.image_paint.brush.curve_preset = 'CONSTANT'

		marquee_corner_a = (self.marquee_start[0], self.marquee_end[1])
		marquee_corner_b = (self.marquee_end[0], self.marquee_start[1])

		locations = [self.marquee_start, marquee_corner_a, self.marquee_end, marquee_corner_b, self.marquee_start]

		# Frame Selected
		bpy.ops.image.view_all()

		for location in locations:
			bpy.ops.paintcurve.add_point(location=location)

		bpy.ops.paintcurve.draw()	

		# New fill method using Curves

		for location in locations:
			bpy.ops.paintcurve.select(location=location)
			bpy.ops.paintcurve.delete_point()

		mid_point = self.marquee_end[0] - self.marquee_start[0]
		mid_point += self.marquee_start[0]
		mid_point = (mid_point, self.marquee_start[1])

		bpy.ops.paintcurve.add_point(location=mid_point)
		bpy.ops.paintcurve.add_point(location=mid_point) # Need 2 curves

		MATTEPAINTER_FN_useImageEditor(True)

		

		material = self.active_object.data.materials[0]
		nodes = material.node_tree.nodes
		links = material.node_tree.links

		mask = nodes.get("transparency_mask")
		mask_image = mask.image 

		#mouse_pos = Vector(((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y))

		self._fill_marquee()

		MATTEPAINTER_FN_useImageEditor(False)

		# Return settings to previous state and delete curve		
		bpy.context.tool_settings.image_paint.brush.paint_curve = None
		bpy.context.tool_settings.image_paint.brush.stroke_method = previous_stroke_method
		bpy.context.tool_settings.image_paint.brush.curve_preset = previous_falloff		

		'''

		if self._in_bounds(self.marquee_start, self.top_left_2d, self.bottom_right_2d):
			offset_x = self.marquee_start[0] - self.top_left_2d[0]
			offset_x_percentage = (offset_x / self.scale_2d[0])
			window = context.window_manager.windows[0]
			# need to get active image
			material = self.active_object.data.materials[0]
			nodes = material.node_tree.nodes
			links = material.node_tree.links

			mask = nodes.get("transparency_mask")
			mask_image = mask.image 

			local_x = int(mask.image.size[0] * offset_x_percentage)
			local_y = int(mask.image.size[1] * .5)

		

		

		elif self._in_bounds(marquee_corner_a, self.top_left_2d, self.bottom_right_2d):
			print('Corner A in Bounds')
		elif self._in_bounds(self.marquee_end, self.top_left_2d, self.bottom_right_2d):
			print('Mouse Up in Bounds')
		elif self._in_bounds(marquee_corner_b, self.top_left_2d, self.bottom_right_2d):
			print('Corner B in Bounds')
		else:
			print('no corners in bounds')
		'''



	@classmethod
	def poll(cls, context):
		return context.mode in ['PAINT_TEXTURE']

	def modal(self, context:bpy.types.Context, event:bpy.types.Event):
		context.area.tag_redraw()
		

		if event.type == 'MOUSEMOVE' and self.mouse_down:

			mouse_current_position = Vector(((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y)) 
			self.mouse_positions[1] = mouse_current_position

			marquee_width = self.mouse_positions[1][0] - self.mouse_positions[0][0]
			marquee_height = self.mouse_positions[0][1] - self.mouse_positions[1][1]		

		elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':

			self.marquee_end = ((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y)

			self._draw_marquee()	

			bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')				
			return{'FINISHED'}

		elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':

			# Setup basic logic
			self.mouse_down = True
			area = MATTEPAINTER_FN_contextOverride("VIEW_3D")
			bpy.context.temp_override(area=area)	
			

			# dont think I need this anymore
			for area in bpy.context.screen.areas:
			    if area.type=='VIEW_3D':
			        X= area.x
			        Y= area.y
			        WIDTH=area.width
			        HEIGHT=area.height		
			
			# Grab Mouse Down vector			
			mouse_down_position = Vector(((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y))
			self.mouse_positions.append(mouse_down_position)
			self.mouse_positions.append(mouse_down_position) # Not a mistake, need to append twice
			start_position = self.mouse_positions[0]			
			
			

			# Check if click was inside object
			in_bounds = self._in_bounds(start_position, self.top_left_2d, self.bottom_right_2d)			

			self.marquee_start = ((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y)

			

			'''

			if in_bounds:
				# calculate where in object click happened
				print(f'Area of Clicked Object: {scale_2d}')

				# mouse x - left edge distance from 0 

				offset_x = start_position[0] - top_left_2d[0]
				offset_x_percentage = (offset_x / scale_2d[0])

				print(f'Offset in pixels: {offset_x}')
				print(f'Offset in % : {offset_x_percentage * 100}%')

				active_object = bpy.context.active_object
				material = active_object.data.materials[0]
				nodes = material.node_tree.nodes
				mask = nodes.get("transparency_mask")
				image = mask.image 
				width = image.size[0]
				height = image.size[1]
				colour = (0.0, 0.0, 0.0, 1.0)
				pixels = [1.0] * (4 * width * height)
				print(len(image.pixels))
				#pixels = image.pixels[:]

				num_pixels = len(pixels)
				num_pixels_to_change = int(num_pixels * offset_x_percentage)

				for i in range(num_pixels_to_change, num_pixels):
					pixels[i] = 0.0

				image.pixels = pixels
				# pixels are calculated from bottom row left to right
				# alpha clip resolves weird line at the top.
				# IMPORTANT: need to add a pixel skipping method
				# 	basically it would just jump ahead {WIDTH} num pixels to get to the next row
				# OR 
				# split the pixels into rows by {WIDTH} then run processing on each individual row, then recombine
				# use pythons split stuff it [:, :, :] using width somewherein there

			else:
				print('missed the mesh')	

			'''


		elif event.type in {'RIGHTMOUSE', 'ESC'}:
			# Remove screen draw
			bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')	
			self.mouse_down = False
			
			return {'FINISHED'}

		return {'RUNNING_MODAL'}
		
	def invoke(self, context, event):	
		self.mouse_positions = []		
		self._mouseover_positions = []
		self.marquee_start = (0, 0)
		self.marquee_end = (0, 0)
		args = (self, context)
		self._handle = bpy.types.SpaceView3D.draw_handler_add(MATTEPAINTER_FN_drawMarqueeCallback, args, 'WINDOW', 'POST_PIXEL')

		self.active_object = bpy.context.active_object
		self.region = bpy.context.region 
		self.region3d = bpy.context.space_data.region_3d 
		self.object_size = self.active_object.dimensions	

		# Grab Vertices			
		self.vertices = self.active_object.data.vertices
		self.top_left = self.vertices[2].co
		self.top_left_2d = view3d_utils.location_3d_to_region_2d(self.region, self.region3d, self.top_left)
		self.bottom_right = self.vertices[1].co
		self.bottom_right_2d = view3d_utils.location_3d_to_region_2d(self.region, self.region3d, self.bottom_right)

		# Use Vertices to Calculate Screen-Based Area of Object
		self.width_2d = self.bottom_right_2d[0] - self.top_left_2d[0]
		self.height_2d = self.top_left_2d[1] - self.bottom_right_2d[1]
		self.scale_2d = (self.width_2d, self.height_2d)

		context.window_manager.modal_handler_add(self)
		return {'RUNNING_MODAL'}


class MATTEPAINTER_OT_selectionLasso(bpy.types.Operator):

	# Not Implemented
	bl_idname = "mattepainter.select_lasso"
	bl_label = "Lasso Fill"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Selects pixels using a Lasso-style selection"

	def _draw_lasso(self):
		bpy.ops.wm.tool_set_by_id(name="builtin_brush.Draw")
		bpy.data.brushes["TexDraw"].stroke_method = 'CURVE'

		previous_stroke_method = bpy.context.tool_settings.image_paint.brush.stroke_method
		previous_falloff = bpy.context.tool_settings.image_paint.brush.curve_preset

		bpy.ops.paintcurve.new()		
		bpy.data.paint_curves[-1].name = "MATTEPAINTER_LassoCurve"

		for point in self.lasso_points:
			bpy.ops.paintcurve.add_point(location=(int(point[0]), int(point[1]))) # fix the list later (remove the Vector crap)

		# Close & Paint Curve
		bpy.ops.paintcurve.add_point(location=(int(self.lasso_points[0][0]), int(self.lasso_points[0][1])))
		bpy.ops.paintcurve.draw()

		# Return settings to previous state and delete curve		
		bpy.context.tool_settings.image_paint.brush.paint_curve = None
		bpy.context.tool_settings.image_paint.brush.stroke_method = previous_stroke_method
		bpy.context.tool_settings.image_paint.brush.curve_preset = previous_falloff	

	strokes = []
	lasso_points = []
	pixels = []
	mouse_down = False

	# while mouse is down, append each mouse position 
	# at mouseup, store final mouse position
	# increment towards mouse position[0] by 1px ie:
	 	# calculate which offset (distance between the start and end) is larger
	 	# for i range Math.abs((that offset ^^))
			# increment.x = final.x +1 if final.x < initial.x else final.x-1
			# increment.y = final.y +1 if final.y < initial.y else final.y-1
			# positions.append((increment.x, increment.y))
		# something like that

	# once lasso is closed, calculate a bounding box around the selection using min/max

	# for each pixel in the bounding box, check if that pixel is inside the lasso 
		# for y in range len(height_of_box)
			# for x in range (width of box)
				# if pixel[x] is in bounding box					
					# might need to raycast horizontally and make sure it passes the lasso ODD times 
					# append pixel
		# paint pixels


	@classmethod
	def poll(cls, context):
		return context.mode in ['PAINT_TEXTURE']

	def modal(self, context:bpy.types.Context, event:bpy.types.Event):
		context.area.tag_redraw()
		if event.type == 'LEFTMOUSE':
			if not self.mouse_down:
				self.mouse_down = True
			else:
				self.mouse_down = False

				# Determine the bounding rectangle of the lassoed area
				min_x = int(min(self.lasso_points, key=lambda p: p[0])[0])
				max_x = int(max(self.lasso_points, key=lambda p: p[0])[0])
				min_y = int(min(self.lasso_points, key=lambda p: p[1])[1])
				max_y = int(max(self.lasso_points, key=lambda p: p[1])[1])

				for x in range(min_x, max_x + 1):
					for y in range(min_y, max_y + 1):
						self.pixels.append((x, y))

				print(f'Num Points: {len(self.lasso_points)}')
				print(f'Num Pixels in Lasso: {len(self.pixels)}')
				print(f'Min X: {min_x}, Max X: {max_x}')
				print(f'Min Y: {min_y}, Max Y: {max_y}')

				self._draw_lasso()

				# Clear array
				self.lasso_points.clear()
				self.pixels.clear()		

				# Remove screen draw
				bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')	

				return{'FINISHED'}

		elif event.type == 'MOUSEMOVE' and self.mouse_down:
			mouse_pos = Vector(((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y))
			self.lasso_points.append(mouse_pos)
			
			# Append mouse positions for screen-draw
			self.mouse_path.append((event.mouse_region_x, event.mouse_region_y))             

		elif event.type in {'RIGHTMOUSE', 'ESC'}:
			# Remove screen draw
			bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
			return {'FINISHED'}

		return {'RUNNING_MODAL'}

	def invoke(self, context, event):
		self.mouse_path = []
		args = (self, context)
		self._handle = bpy.types.SpaceView3D.draw_handler_add(MATTEPAINTER_FN_drawLassoCallback, args, 'WINDOW', 'POST_PIXEL')

		context.window_manager.modal_handler_add(self)
		return {'RUNNING_MODAL'}	

class MATTEPAINTER_OT_fillAll(bpy.types.Operator):
	# Implementation of Krita's fill all pixels function
	# Shortcut is Shift+Backspace in Texture Paint Mode

	# NOTES
	# because Paint Bucket and Brush Tool have different Colour Palletes, need to pull colour from Brush Tool
	# also need to revert back to Brush Tool when done

	# Not Implemented
	bl_idname = "mattepainter.fill_all"
	bl_label = "Fill All"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Fills all pixels with the foreground colour."

	@classmethod
	def poll(cls, context):
		return context.mode in ['PAINT_TEXTURE']

	def execute(self, context):	
		active_object = bpy.context.active_object
		bpy.ops.wm.tool_set_by_id(name="builtin_brush.Fill")
		area = MATTEPAINTER_FN_contextOverride("VIEW_3D")
		bpy.context.temp_override(area=area)	

		region = bpy.context.region 
		region3d = bpy.context.space_data.region_3d 
		object_location = view3d_utils.location_3d_to_region_2d(region, region3d, active_object.location)

		stroke = [{"name": "",
				    "is_start": False,
				    "location": (0,0,0),
				    "mouse":(object_location[0], object_location[1]),
				    "mouse_event":(0,0),
				    "pen_flip":False,
				    "pressure":1.0,
				    "size":1,
				    "time":0,
				    "x_tilt":0.0,
				    "y_tilt":0.0}]

		bpy.ops.paint.image_paint(stroke=stroke)
		return {'FINISHED'}	

#--------------------------------------------------------------
# Interface
#--------------------------------------------------------------

class MATTEPAINTER_PT_panelMain(bpy.types.Panel):
	bl_label = "MattePainter"
	bl_idname = "MATTEPAINTER_PT_panelMain"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'MattePainter'

	def draw(self, context):
		layout = self.layout		

class MATTEPAINTER_PT_panelLayers(bpy.types.Panel):
	bl_label = "Layers"
	bl_idname = "MATTEPAINTER_PT_panelLayers"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'MattePainter'
	bl_parent_id = 'MATTEPAINTER_PT_panelMain'

	def draw(self, context):
		layout = self.layout
		view = context.space_data
		scene = context.scene

		# Import, Empty Layer & Paint Buttons
		row = layout.row()
		row.operator(MATTEPAINTER_OT_newLayerFromFile.bl_idname, text="Import", icon="FILE_IMAGE")
		row.operator(MATTEPAINTER_OT_newLayerFromClipboard.bl_idname, text="Paste Clipboard", icon="PASTEDOWN")
		
		row = layout.row()
		row.operator(MATTEPAINTER_OT_newEmptyPaintLayer.bl_idname, text="New Layer", icon="FILE_NEW")
		row.operator(MATTEPAINTER_OT_paintMask.bl_idname, text="Paint", icon="BRUSH_DATA")

		# Make Unique & Move To Camera
		row = layout.row()
		row.operator(MATTEPAINTER_OT_makeUnique.bl_idname, text="Make Unique", icon="DUPLICATE")
		row.operator(MATTEPAINTER_OT_moveToCamera.bl_idname, text="To Camera", icon="OUTLINER_OB_CAMERA")

		# Paint & Selection Tools
		row = layout.row()
		row.operator(MATTEPAINTER_OT_toolBrush.bl_idname, text="", icon="BRUSHES_ALL", emboss=True, depress=True if bpy.data.brushes["TexDraw"].stroke_method == 'SPACE' else False)
		row.operator(MATTEPAINTER_OT_toolLine.bl_idname, text="", icon="IPO_LINEAR", emboss=True, depress=True if bpy.data.brushes["TexDraw"].stroke_method == 'LINE' else False)
		row.operator(MATTEPAINTER_OT_selectionMarquee.bl_idname, text="", icon="SELECT_SET")
		row.operator(MATTEPAINTER_OT_selectionLasso.bl_idname, text="", icon="MOD_DASH")
		row.operator(MATTEPAINTER_OT_fillAll.bl_idname, text="", icon="SNAP_FACE", emboss=True)

		if bpy.data.collections.find(r"MattePainter") != -1 and len(bpy.data.collections[r"MattePainter"].objects) > 0:
			box = layout.box()
			box.enabled = True
			box.alert = False
			box.scale_x = 1.0
			box.scale_y = 1.0
			for i in range(len(bpy.data.collections[r"MattePainter"].objects)):
				row = box.row(align=False)
				row.enabled = True 
				row.alert = False
				row.scale_x = 1.0
				row.scale_y = 0.85

				layer_object = bpy.data.collections[r"MattePainter"].objects[i]
				if layer_object.type != 'MESH':
					return
				layer_nodes = layer_object.data.materials[0].node_tree.nodes

				opSelect = row.operator(MATTEPAINTER_OT_layerSelect.bl_idname, text=layer_object.name, emboss=True if context.active_object==layer_object else False, depress=True if context.active_object==layer_object else False, icon_value=0) 
				opVisible = row.operator(MATTEPAINTER_OT_layerVisibility.bl_idname, text="", emboss=False, depress=True, icon_value=253 if layer_object.hide_render else 254)	
				opLock = row.operator(MATTEPAINTER_OT_layerLock.bl_idname, text="", emboss=False, depress=True, icon_value=41 if layer_object.hide_select else 224)	
				opInvertMask = row.operator(MATTEPAINTER_OT_layerInvertMask.bl_idname, text="", emboss=False, depress=True, icon='CLIPUV_HLT' if layer_nodes.get('invert').mute else 'CLIPUV_DEHLT')	
				if not layer_nodes.get('transparency_mask') == None:
					opShowMask = row.operator(MATTEPAINTER_OT_layerShowMask.bl_idname, text="", emboss=False, depress=True, icon='IMAGE_ALPHA' if layer_nodes.get('transparency_mask').outputs[0].links[0].to_node.name == 'invert' else 'IMAGE_RGB')	

				opSelect.MATTEPAINTER_VAR_layerIndex = i
				opVisible.MATTEPAINTER_VAR_layerIndex = i
				opLock.MATTEPAINTER_VAR_layerIndex = i
				opInvertMask.MATTEPAINTER_VAR_layerIndex = i
				opShowMask.MATTEPAINTER_VAR_layerIndex = i


class MATTEPAINTER_PT_panelFileManagement(bpy.types.Panel):
	bl_label = "File Management"
	bl_idname = "MATTEPAINTER_PT_panelFileManagement"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'MattePainter'
	bl_parent_id = 'MATTEPAINTER_PT_panelMain'

	def draw(self, context):
		layout = self.layout

		# Save All Button
		row = layout.row()
		row.operator(MATTEPAINTER_OT_saveAllImages.bl_idname, text="Save All", icon_value=727)
		row.operator(MATTEPAINTER_OT_clearUnused.bl_idname, text="Clear Unused", icon_value=21)
		

		# Make Sequence 
		if (not bpy.context.active_object == None and bpy.context.active_object.type == 'MESH' and bpy.context.active_object.users_collection[0] == bpy.data.collections['MattePainter']):
			if bpy.context.active_object.data.materials[0].node_tree.nodes.get('albedo').image.source == 'FILE':
				row.operator(MATTEPAINTER_OT_makeSequence.bl_idname, text='To Sequence', icon="SEQUENCE")

		# Cycles Layers
		row = layout.row()
		row.prop(bpy.context.scene.view_settings,'view_transform',icon_value=54, text=r"Color", emboss=True, expand=False,)
		row.prop(bpy.context.scene.cycles,'transparent_max_bounces', text=r"Cycles Layers:", emboss=True, slider=False,)
		

class MATTEPAINTER_PT_panelColorGrade(bpy.types.Panel):
	bl_label = "Color Grade"
	bl_idname = "MATTEPAINTER_PT_panelColorGrade"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'UI'
	bl_category = 'MattePainter'
	bl_parent_id = 'MATTEPAINTER_PT_panelMain'

	def draw(self, context):
		if not bpy.context.active_object == None and not bpy.context.active_object.type == 'MESH':
			return
		layout = self.layout
		if (not bpy.context.active_object == None and bpy.context.active_object.users_collection[0] == bpy.data.collections['MattePainter']):
			box = layout.box()
			box.enabled = True
			box.alert = False
			box.scale_x = 1.0
			box.scale_y = 1.0			
			box.prop(bpy.context.active_object.data.materials[0].node_tree.nodes[r"opacity"].inputs[0], 'default_value', text=r"Opacity", emboss=True, slider=True)
			box.prop(bpy.context.active_object.data.materials[0].node_tree.nodes[r"blur_mix"].inputs[0], 'default_value', text=r"Blur", emboss=True, slider=True)
			sn_layout = box
			sn_layout.template_curve_mapping(bpy.context.active_object.data.materials[0].node_tree.nodes[r"curves"], 'mapping', type='COLOR')
			box.prop(bpy.context.active_object.data.materials[0].node_tree.nodes[r"HSV"].inputs[0], 'default_value', text=r"Hue", emboss=True, slider=True)
			box.prop(bpy.context.active_object.data.materials[0].node_tree.nodes[r"HSV"].inputs[1], 'default_value', text=r"Saturation", emboss=True, slider=True)
			box.prop(bpy.context.active_object.data.materials[0].node_tree.nodes[r"HSV"].inputs[2], 'default_value', text=r"Value", emboss=True, slider=True)


addon_keymaps = []

#--------------------------------------------------------------
# Register 
#--------------------------------------------------------------

def register():
	# Interface
	bpy.utils.register_class(MATTEPAINTER_PT_panelMain)
	bpy.utils.register_class(MATTEPAINTER_PT_panelLayers)
	bpy.utils.register_class(MATTEPAINTER_PT_panelFileManagement)
	bpy.utils.register_class(MATTEPAINTER_PT_panelColorGrade)

	# Functionality
	bpy.utils.register_class(MATTEPAINTER_OT_newLayerFromFile)
	bpy.utils.register_class(MATTEPAINTER_OT_newEmptyPaintLayer)
	bpy.utils.register_class(MATTEPAINTER_OT_newLayerFromClipboard)
	bpy.utils.register_class(MATTEPAINTER_OT_paintMask)
	bpy.utils.register_class(MATTEPAINTER_OT_makeUnique)
	bpy.utils.register_class(MATTEPAINTER_OT_makeSequence)
	bpy.utils.register_class(MATTEPAINTER_OT_saveAllImages)
	bpy.utils.register_class(MATTEPAINTER_OT_clearUnused)
	bpy.utils.register_class(MATTEPAINTER_OT_layerSelect)
	bpy.utils.register_class(MATTEPAINTER_OT_layerVisibility)
	bpy.utils.register_class(MATTEPAINTER_OT_layerLock)
	bpy.utils.register_class(MATTEPAINTER_OT_layerInvertMask)
	bpy.utils.register_class(MATTEPAINTER_OT_layerInvertMaskActive)	
	bpy.utils.register_class(MATTEPAINTER_OT_layerShowMask)
	bpy.utils.register_class(MATTEPAINTER_OT_moveToCamera)

	bpy.utils.register_class(MATTEPAINTER_OT_toolBrush)
	bpy.utils.register_class(MATTEPAINTER_OT_toolLine)
	bpy.utils.register_class(MATTEPAINTER_OT_selectionMarquee)
	bpy.utils.register_class(MATTEPAINTER_OT_selectionLasso)	
	bpy.utils.register_class(MATTEPAINTER_OT_fillAll)

	# Variables
	bpy.types.Object.MATTEPAINTER_VAR_layerIndex = bpy.props.IntProperty(name='MATTEPAINTER_VAR_layerIndex',description='',subtype='NONE',options=set(), default=0)

	# Keymaps
	wm = bpy.context.window_manager
	kc = wm.keyconfigs.addon 
	if kc:
		# Paste Clipboard
		km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
		kmi = km.keymap_items.new(MATTEPAINTER_OT_newLayerFromClipboard.bl_idname, type='V', value='PRESS', shift=True, ctrl=True)
		addon_keymaps.append((km, kmi))

		# Brush Tool
		kmi = km.keymap_items.new(MATTEPAINTER_OT_toolBrush.bl_idname, type='B', value='PRESS')
		addon_keymaps.append((km, kmi))

		# Line Tool
		kmi = km.keymap_items.new(MATTEPAINTER_OT_toolLine.bl_idname, type='B', value='PRESS', shift=True)
		addon_keymaps.append((km, kmi))

		# Fill All
		kmi = km.keymap_items.new(MATTEPAINTER_OT_fillAll.bl_idname, type='BACK_SPACE', value='PRESS', shift=True)
		addon_keymaps.append((km, kmi))

		# Make Unique
		kmi = km.keymap_items.new(MATTEPAINTER_OT_makeUnique.bl_idname, type='D', value='PRESS', shift=True, ctrl=True)
		addon_keymaps.append((km, kmi))

		# Invert Active Layer Mask
		kmi = km.keymap_items.new(MATTEPAINTER_OT_layerInvertMaskActive.bl_idname, type='I', value='PRESS', shift=True, ctrl=True)
		addon_keymaps.append((km, kmi))

		# Switch to Paint Mode
		kmi = km.keymap_items.new(MATTEPAINTER_OT_paintMask.bl_idname, type='LEFTMOUSE', value='PRESS', shift=True, ctrl=True)
		addon_keymaps.append((km, kmi))
		
		


def unregister():
	# Interface
	bpy.utils.unregister_class(MATTEPAINTER_PT_panelMain)
	bpy.utils.unregister_class(MATTEPAINTER_PT_panelLayers)
	bpy.utils.unregister_class(MATTEPAINTER_PT_panelFileManagement)
	bpy.utils.unregister_class(MATTEPAINTER_PT_panelColorGrade)

	# Functionality
	bpy.utils.unregister_class(MATTEPAINTER_OT_newLayerFromFile)
	bpy.utils.unregister_class(MATTEPAINTER_OT_newEmptyPaintLayer)
	bpy.utils.unregister_class(MATTEPAINTER_OT_newLayerFromClipboard)
	bpy.utils.unregister_class(MATTEPAINTER_OT_paintMask)
	bpy.utils.unregister_class(MATTEPAINTER_OT_makeUnique)
	bpy.utils.unregister_class(MATTEPAINTER_OT_makeSequence)
	bpy.utils.unregister_class(MATTEPAINTER_OT_saveAllImages)
	bpy.utils.unregister_class(MATTEPAINTER_OT_clearUnused)
	bpy.utils.unregister_class(MATTEPAINTER_OT_layerSelect)
	bpy.utils.unregister_class(MATTEPAINTER_OT_layerVisibility)
	bpy.utils.unregister_class(MATTEPAINTER_OT_layerLock)
	bpy.utils.unregister_class(MATTEPAINTER_OT_layerInvertMask)
	bpy.utils.unregister_class(MATTEPAINTER_OT_layerInvertMaskActive)	
	bpy.utils.unregister_class(MATTEPAINTER_OT_layerShowMask)
	bpy.utils.unregister_class(MATTEPAINTER_OT_moveToCamera)

	bpy.utils.unregister_class(MATTEPAINTER_OT_toolBrush)
	bpy.utils.unregister_class(MATTEPAINTER_OT_toolLine)
	bpy.utils.unregister_class(MATTEPAINTER_OT_selectionMarquee)
	bpy.utils.unregister_class(MATTEPAINTER_OT_selectionLasso)
	bpy.utils.unregister_class(MATTEPAINTER_OT_fillAll)	
	

	# Variables

	del bpy.types.Object.MATTEPAINTER_VAR_layerIndex

	# Keymaps
	for km, kmi in addon_keymaps:
		km.keymap_items.remove(kmi)

	addon_keymaps.clear()

if __name__ == "__main__":
	register()