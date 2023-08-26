#--------------------------------------------------------------
# Meta Dictionary
#--------------------------------------------------------------

bl_info = {
	"name" : "test_paint_fill",
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

import bpy
import bpy_extras
from mathutils import Vector
from math import floor
import time 
import numpy as np 

# Draw Functions
import blf
import gpu
from gpu_extras.batch import batch_for_shader

def get_override(area_type, region_type):
	for area in bpy.context.screen.areas: 
		if area.type == area_type:             
			for region in area.regions:                 
				if region.type == region_type:                    
					override = {'area': area, 'region': region} 
					return override					

def drawMarqueeCallback(self, context):
	font_id = 0

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

		# DEBUG

		blf.color(font_id, 1.0, 1.0, 1.0, 0.7)
		blf.position(font_id, end_vert[0] + 50, end_vert[1], 0)
		blf.size(font_id, 14)
		blf.draw(font_id, 'DEBUG')

		# MOUSE_POSITION_SCREEN

		blf.color(font_id, 1.0, 1.0, 1.0, 0.5)
		blf.position(font_id, end_vert[0] + 50, end_vert[1] - 20, 0)
		blf.size(font_id, 12)
		blf.draw(font_id, f'{end_vert}')

		# PIXEL POSITION

		blf.color(font_id, 1.0, 1.0, 1.0, 0.5)
		blf.position(font_id, end_vert[0] + 50, end_vert[1] - 40, 0)
		blf.size(font_id, 12)
		blf.draw(font_id, f'{self.pixel_coords_current}')

class selectionMarquee2D(bpy.types.Operator):
	bl_idname = "fill_tool.select_marquee_2d"
	bl_label = "Marquee Fill"
	bl_options = {"REGISTER", "UNDO"}
	bl_description = "Fills pixels using a Marquee-style selection"		

	def _calculate_center(self, x_min, x_max, y_min, y_max):
		x_mu = int((x_min + x_max) / 2)
		y_mu = int((y_min + y_max) / 2)

		return (x_mu, y_mu)	

	def _out_of_bounds_check(self):
		if self.pixel_coords_down[0] < 0 and self.pixel_coords_up[0] < 0:
			return False
		if self.pixel_coords_down[0] > self.image.size[0] and self.pixel_coords_up[0] > self.image.size[0]:
			return False
		if self.pixel_coords_down[1] < 0 and self.pixel_coords_up[1] < 0:
			return False
		if self.pixel_coords_down[1] > self.image.size[1] and self.pixel_coords_up[1] > self.image.size[1]:
			return False
		return True

	def _orient_marquee(self, x1, y1, x2, y2, image):
		# Orients the Marquee for each potential mouse position
		# Also runs a boundary check
		self.x_orient = 'left_to_right' if x1 < x2 else 'right_to_left'
		self.y_orient = 'bottom_to_top' if y1 < y2 else 'top_to_bottom'	

		if self.x_orient == 'left_to_right':
			x1 = max(x1, 0)
			x2 = min(x2, image.size[0] - 1)
		else:
			x1 = min(x1, image.size[0] - 1)
			x2 = max(x2, 0)
		if self.y_orient == 'bottom_to_top':
			y1 = max(y1, 0)
			y2 = min(y2, image.size[1] - 1)
		else:
			y1 = min(y1, image.size[1] - 1)
			y2 = max(y2, 0)

		if x1 > x2:
			x1, x2 = x2, x1
		if y1 > y2:
			y1, y2 = y2, y1

		return x1, y1, x2, y2

	def _convert_pixel_buffer_to_matrix(self, buffer, width, height, channels):
		# Converts a 1-D pixel buffer into an xy grid with n Colour channels
		buffer = buffer.reshape(height, width, channels)
		return buffer

	def _convert_matrix_to_pixel_buffer(self, buffer):
		# Converts back to 1-D pixel buffer
		buffer = buffer.flatten()
		return buffer

	def _get_color(self, use_bg=False):
		# Grabs active Paint Brush colour 
		r = bpy.context.tool_settings.image_paint.brush.color[0] if use_bg==False else bpy.context.tool_settings.image_paint.brush.secondary_color[0]
		g = bpy.context.tool_settings.image_paint.brush.color[1] if use_bg==False else bpy.context.tool_settings.image_paint.brush.secondary_color[1]
		b = bpy.context.tool_settings.image_paint.brush.color[2] if use_bg==False else bpy.context.tool_settings.image_paint.brush.secondary_color[2]
		a = bpy.context.tool_settings.image_paint.brush.strength
		return [r, g, b, a]

	def _get_transparency_mask_image(self):
		# Selects the mask layer
		self.active_object = bpy.context.active_object
		material = self.active_object.data.materials[0]
		nodes = material.node_tree.nodes
		mask = nodes.get("transparency_mask")
		image = mask.image 

		return image

	def _fill_pixels(self, x1, y1, x2, y2, brush_color):
		# Fills the marquee pixels and inserts them into the Image Matrix
		
		pixels_to_paint = np.ones(4 * self.image.size[0] * self.image.size[1], dtype=np.float32)

		# Orient Marquee
		x1, y1, x2, y2 = self._orient_marquee(x1, y1, x2, y2, self.image)

		self.image.pixels.foreach_get(pixels_to_paint)	
		
		marquee_height = int(y2 - y1)
		marquee_width = int(x2 - x1)

		pixels_to_paint = self._convert_pixel_buffer_to_matrix(pixels_to_paint, self.image.size[0], self.image.size[1], 4)

		marquee_fill = np.zeros(4 * marquee_width * marquee_height)
		marquee_fill = self._convert_pixel_buffer_to_matrix(marquee_fill, marquee_width, marquee_height, 4)		

		marquee_fill[:][:] = brush_color

		pixels_to_paint[y1:y2, x1:x2, :] = marquee_fill	

		pixels_to_paint = self._convert_matrix_to_pixel_buffer(pixels_to_paint)

		self.image.pixels.foreach_set(pixels_to_paint)
		self.image.update()

	def _get_2d_mouse_coords(self, context, event):
		# Calculates current pixel at mouseover point
		region = context.region
		reg_x, reg_y = event.mouse_region_x, event.mouse_region_y
		img_size = context.area.spaces[0].image.size
		
		uv_x, uv_y = region.view2d.region_to_view(reg_x, reg_y)
		
		img_x, img_y = uv_x * img_size[0], uv_y * img_size[1]

		return int(img_x), int(img_y)	

	@classmethod
	def poll(cls, context):
		return bpy.context.space_data.ui_mode in ['PAINT']

	def modal(self, context:bpy.types.Context, event:bpy.types.Event):
		context.area.tag_redraw()		

		if event.type == 'MOUSEMOVE' and self.mouse_down:

			mouse_current_position = Vector(((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y)) 
			self.mouse_positions[1] = mouse_current_position

			marquee_width = self.mouse_positions[1][0] - self.mouse_positions[0][0]
			marquee_height = self.mouse_positions[0][1] - self.mouse_positions[1][1]

			self.pixel_coords_current = self._get_2d_mouse_coords(context, event)		

		elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':	
			self.image = self._get_transparency_mask_image()		
			self.marquee_end = ((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y)
			self.pixel_coords_up = self._get_2d_mouse_coords(context, event)	

			# Out of Bounds Check
			if self._out_of_bounds_check() == False:
				return{'CANCELLED'}

			brush_color = self._get_color(use_bg=event.ctrl)

			self._fill_pixels(self.pixel_coords_down[0], self.pixel_coords_down[1], self.pixel_coords_up[0], self.pixel_coords_up[1], brush_color=brush_color)

			bpy.types.SpaceImageEditor.draw_handler_remove(self._handle, 'WINDOW')				
			return{'FINISHED'}

		elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':

			self.mouse_down = True
			
			# Grab Mouse Down vector			
			mouse_down_position = Vector(((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y))
			self.mouse_positions.append(mouse_down_position)
			self.mouse_positions.append(mouse_down_position) # Not a mistake, need to append twice
			start_position = self.mouse_positions[0]			

			self.marquee_start = ((event.mouse_x) - context.area.regions.data.x, event.mouse_y - context.area.regions.data.y)

			self.pixel_coords_down = self._get_2d_mouse_coords(context, event)
			

		elif event.type in {'RIGHTMOUSE', 'ESC'}:
			bpy.types.SpaceImageEditor.draw_handler_remove(self._handle, 'WINDOW')	
			self.mouse_down = False
			
			return {'FINISHED'}

		return {'RUNNING_MODAL'}
		
	def invoke(self, context, event):	
		self.mouse_positions = []		
		self.marquee_start = (0, 0)
		self.marquee_end = (0, 0)
		self.pixel_coords_down = (0, 0)
		self.pixel_coords_up = (0, 0)
		self.pixel_coords_current = (0, 0)
		self.mouse_down = False
		self.image = None

		self.x_orient = ''
		self.y_orient = ''

		args = (self, context)
		self._handle = bpy.types.SpaceImageEditor.draw_handler_add(drawMarqueeCallback, args, 'WINDOW', 'POST_PIXEL')
		self.active_object = bpy.context.active_object

		context.window_manager.modal_handler_add(self)
		return {'RUNNING_MODAL'}

#--------------------------------------------------------------
# Interface
#--------------------------------------------------------------

class fill_tool_panel(bpy.types.Panel):
	bl_label = "fill_tool_panel"
	bl_idname = "MATTEPAINTER_PT_fill_tool_panel"
	bl_space_type = 'IMAGE_EDITOR'
	bl_region_type = 'UI'
	bl_category = 'fill_tool'

	def draw(self, context):
		layout = self.layout	

class fill_tool_panel2(bpy.types.Panel):
	bl_label = "fill_tool_panel2"
	bl_idname = "MATTEPAINTER_PT_fill_tool_panel2"
	bl_space_type = 'IMAGE_EDITOR'
	bl_region_type = 'UI'
	bl_category = 'fill_tool'
	bl_parent_id = 'MATTEPAINTER_PT_fill_tool_panel'

	def draw(self, context):
		layout = self.layout
		view = context.space_data
		scene = context.scene
		row = layout.row()
		row.operator(selectionMarquee2D.bl_idname, text="Marquee", icon="FILE_IMAGE")


#--------------------------------------------------------------
# Register 
#--------------------------------------------------------------

def register():
	# Interface
	bpy.utils.register_class(fill_tool_panel)
	bpy.utils.register_class(fill_tool_panel2)

	# Functionality
	bpy.utils.register_class(selectionMarquee2D)
	

def unregister():
	# Interface
	bpy.utils.unregister_class(fill_tool_panel)
	bpy.utils.unregister_class(fill_tool_panel2)

	# Functionality
	bpy.utils.unregister_class(selectionMarquee2D)



if __name__ == "__main__":
	register()