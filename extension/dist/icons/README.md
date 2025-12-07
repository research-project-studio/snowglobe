# Extension Icons

Icon files needed:
- icon-16.png (16x16)
- icon-48.png (48x48)
- icon-128.png (128x128)

## Temporary Solution

For development, you can create simple placeholder icons:

```bash
# Using ImageMagick (if available)
convert -size 16x16 xc:'#4CAF50' icon-16.png
convert -size 48x48 xc:'#4CAF50' icon-48.png
convert -size 128x128 xc:'#4CAF50' icon-128.png
```

Or download icon images and place them in this directory.

## Production Icons

For production, create proper icons with a map/archive theme using:
- Adobe Illustrator / Sketch / Figma
- Online icon generators
- Or commission from a designer
