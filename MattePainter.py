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

		# Shader Setup
		material = bpy.data.materials.new(name=image.name)
		active_object.data.materials.append(material)
		material.blend_method = "HASHED"
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

		# Shader Setup
		material = bpy.data.materials.new(name=image.name)
		active_object.data.materials.append(material)
		material.blend_method = "HASHED"
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

		# Shader Setup
		material = bpy.data.materials.new(name=image.name)
		active_object.data.materials.append(material)
		material.blend_method = "HASHED"
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
			self.report({"WARNING"}, "Images failed to save (are they already saved?)")
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
		return {'FINISHED'}

#--------------------------------------------------------------
# ____NOT_IMPLEMENTED
#--------------------------------------------------------------

class MATTEPAINTER_OT_selectionLasso(bpy.types.Operator):
	# Not Implemented
	bl_idname = "mattepainter.select_lasso"
	bl_label = "Selects pixels using a Lasso-style selection"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Selects pixels using a Lasso-style selection"

	@classmethod
	def poll(cls, context):
		return context.mode in ['PAINT_TEXTURE']

	def modal(self, context:bpy.types.Context, event:bpy.types.Event):
		if event.type == 'LEFTMOUSE':
			mouse_position = Vector(((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y))
			region = bpy.context.region 
			region_data = bpy.context.region_data
			ray_vector = view3d_utils.region_2d_to_vector_3d(region, region_data, mouse_position)
			ray_origin = view3d_utils.region_2d_to_origin_3d(region, region_data, mouse_position)
			direction = ray_origin + (ray_vector * 1000)
			direction -= ray_origin
			result, location, normal, index, obj, matrix = bpy.context.scene.ray_cast(bpy.context.view_layer.depsgraph, ray_origin, direction)

			print('________________________________')
			print(f'Ray Origin: {ray_origin}')
			print(f'Ray Vector: {ray_vector}')
			print(f'Direction: {direction}')
			print(f'Result: {result}')
			print(f'Location: {location}')
			print(f'Matrix: {matrix}')		

		elif event.type in {'RIGHTMOUSE', 'ESC'}:
			return {'FINISHED'}

		return {'RUNNING_MODAL'}

	def invoke(self, context, event):
		context.window_manager.modal_handler_add(self)
		return {'RUNNING_MODAL'}


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

		# Selection Tools
		# Not Implemented
		#row = layout.row()
		#row.operator(MATTEPAINTER_OT_selectionLasso.bl_idname, text="Lasso Select", icon="CONSOLE")

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

				opSelect = row.operator(MATTEPAINTER_OT_layerSelect.bl_idname, text=layer_object.name, emboss=False, depress=False, icon_value=0) 
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
	bpy.utils.register_class(MATTEPAINTER_OT_layerShowMask)
	bpy.utils.register_class(MATTEPAINTER_OT_moveToCamera)

	bpy.utils.register_class(MATTEPAINTER_OT_selectionLasso)

	# Variables
	bpy.types.Object.MATTEPAINTER_VAR_layerIndex = bpy.props.IntProperty(name='MATTEPAINTER_VAR_layerIndex',description='',subtype='NONE',options=set(), default=0)

	# Keymaps
	wm = bpy.context.window_manager
	kc = wm.keyconfigs.addon 
	if kc:
		km = kc.keymaps.new(name='3D View', space_type='VIEW_3D')
		kmi = km.keymap_items.new(MATTEPAINTER_OT_newLayerFromClipboard.bl_idname, type='V', value='PRESS', shift=True, ctrl=True)
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
	bpy.utils.unregister_class(MATTEPAINTER_OT_layerShowMask)
	bpy.utils.unregister_class(MATTEPAINTER_OT_moveToCamera)

	bpy.utils.unregister_class(MATTEPAINTER_OT_selectionLasso)

	# Variables

	del bpy.types.Object.MATTEPAINTER_VAR_layerIndex

	# Keymaps
	for km, kmi in addon_keymaps:
		km.keymap_items.remove(kmi)

	addon_keymaps.clear()

if __name__ == "__main__":
	register()