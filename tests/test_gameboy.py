import unittest

from PIL import Image

from picam.gameboy import gameboy_frame


class GameBoyTests(unittest.TestCase):
    def test_output_size_and_mode(self) -> None:
        src = Image.new("RGB", (640, 480), (120, 80, 40))
        out = gameboy_frame(src, lcd_tint=True, scale=4)
        self.assertEqual(out.size, (128 * 4, 112 * 4))
        self.assertEqual(out.mode, "RGB")

    def test_greyscale_palette(self) -> None:
        src = Image.new("RGB", (200, 200), (200, 200, 200))
        out = gameboy_frame(src, lcd_tint=False, scale=1)
        self.assertEqual(out.size, (128, 112))
        colors = {px for px in out.getdata()}
        allowed = {(0, 0, 0), (85, 85, 85), (170, 170, 170), (255, 255, 255)}
        self.assertTrue(colors.issubset(allowed))


if __name__ == "__main__":
    unittest.main()
