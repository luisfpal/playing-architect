# playing-architect. Needs: python3, uv (https://docs.astral.sh/uv/), node 20+ (only for optimize).
GLB      := build/casa.glb
GLB_URL  ?= https://github.com/luisfpal/playing-architect/releases/download/v1.0/casa.glb
UVPW     := uv run --no-project --python 3.12 --with playwright
PORT     ?= 8080

.PHONY: all glb serve test viewer-test interact-test optimize site deploy clean

all: glb test

# The scene is a release asset (38 MB), not a git object.
glb: $(GLB)
$(GLB):
	mkdir -p build && curl -fL --retry 3 -o $@ $(GLB_URL)

# Local preview with the same server as production (gzip + ETag): http://127.0.0.1:$(PORT)/
serve: site
	cd build/site && python3 ../../deploy/serve.py $(PORT)

# Headless Chromium drives the real viewer with real key, mouse and touch input.
test: viewer-test interact-test

viewer-test: $(GLB)
	$(UVPW) playwright install chromium
	$(UVPW) python viewer/test_viewer.py
	$(UVPW) python viewer/test_sink.py
	$(UVPW) python viewer/test_touch.py
	$(UVPW) python viewer/test_ladder.py
	uv run --no-project --python 3.12 --with pillow --with numpy python tools/luminance.py

interact-test: $(GLB)
	$(UVPW) python viewer/test_interact.py

# Web copy of a raw Blender export: same triangles and nodes, meshopt + lossless WebP. RAW=path/to/raw.glb
optimize:
	cd tools/web && npm ci --no-audit --no-fund --silent
	node tools/web/optimize.mjs $(RAW) $(GLB)

# Static site: landing page + viewer + scene (+ .gz copies the server sends to browsers that accept gzip).
site: $(GLB)
	rm -rf build/site && mkdir -p build/site/build
	cp site/index.html site/base.css build/site/
	cp -R viewer build/site/viewer && rm -f build/site/viewer/test_*.py
	cp $(GLB) build/site/build/casa.glb
	mkdir -p build/site/assets && cp -R assets/hdri build/site/assets/hdri
	find build/site -type f \( -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.json' -o -name '*.glb' -o -name '*.wasm' -o -name '*.hdr' \) -exec gzip -kf9 {} \;
	find build/site -name '*.gz' -exec touch {} +   # serve.py sends FILE.gz only when it is not older than FILE

# Copy the site to a server and (re)start its systemd user unit. HOST=user@server DIR=playing_architect_site PORT=8780
deploy: site
	HOST=$(HOST) DIR=$(DIR) PORT=$(PORT) deploy/deploy.sh

clean:
	rm -rf build/site build/r5_shots build/r9 build/r10 build/*.html
