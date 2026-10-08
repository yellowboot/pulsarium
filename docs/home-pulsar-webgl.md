# Homepage light pulsar

The homepage keeps its original `.pulsar` SVG, spans, inline CSS and shared theme assets. A transparent WebGL canvas covers the artwork after its first successful draw. Integration consists of two asset tags and `data-pulsar-webgl="light"` on `.hero-visual` in `index.html`.

## Restore the original

- For one visit, open `https://pulsarium.finance/?pulsar=original`. This bypasses WebGL completely.
- To restore the original for all visitors, change the homepage attribute to `data-pulsar-webgl="original"`. The existing artwork resumes; no SVG or theme reconstruction is required.
- The complete pre-change source is preserved at branch `backup/home-pulsar-before-webgl-2026-10-08`, commit `0cb6569cf3dedbd6ee2851dec6fab73d4d0ad5b7`.

The original also appears automatically if WebGL 2 is unavailable, a shader cannot compile or link, or the WebGL context is lost. A recovered context resumes the light field.

## Boundaries and budget

Only the homepage loads `assets/pulsar-light.js` and `assets/pulsar-light.css`. All CSS selectors begin with the marked `.hero-visual`. The effect reads the existing `data-color-theme` attribute and creates its own canvas; it does not alter theme storage, navigation, content, forms or analytics.

The animation draws at a maximum of 30 frames per second. Its drawing buffer is capped at 480,000 pixels on desktop and 180,000 pixels on viewports up to 600 CSS pixels wide. The canvas requests a low-power context. It initializes when the artwork becomes visible, stops outside the viewport and in hidden tabs, and freezes its clock while stopped. Reduced motion draws a static frame and updates it when the theme or dimensions change. Hidden original CSS animations are paused while WebGL is active.

The script and stylesheet total approximately 12.3 KB before compression and 4.4 KB with gzip. There are no new image, font, package or API dependencies.

## Validation

Run `node --test tests/pulsar-light.test.cjs` for eight lifecycle and fallback checks, and `node --check assets/pulsar-light.js` for syntax validation. The exact embedded GLSL ES shaders were compiled and rendered with Mesa EGL in Calm and Neon at desktop and mobile dimensions. This checks the rendering code; performance on physical phones still requires measurement.

A source comparison confirmed that removing the two asset tags and the mode attribute restores the original homepage byte for byte. The existing theme batch check reports 56 pages needing an update on both the baseline and this branch; this change leaves that result and all shared theme files unchanged.
