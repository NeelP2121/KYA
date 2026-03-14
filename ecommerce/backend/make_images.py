from PIL import Image

def make_solid_color(filename, hex_color):
    hex_color = hex_color.lstrip('#')
    rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    img = Image.new('RGB', (400, 400), color=rgb)
    img.save(f"static/{filename}")

colors = {
    "image_1.png": "#FF3366",
    "image_2.png": "#33CCFF",
    "image_3.png": "#FFCC00",
    "image_4.png": "#33FF99",
    "image_5.png": "#9933FF"
}

for filename, color in colors.items():
    make_solid_color(filename, color)
    print(f"Created static/{filename} with color {color}")
