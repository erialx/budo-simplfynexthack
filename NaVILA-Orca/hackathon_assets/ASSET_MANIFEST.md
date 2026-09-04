# Hackathon OrcaStudio asset bundle

- `street.json` is the complete exported traffic-crossing scene.
- The scene references OrcaStudio-managed prefabs by their exact `asset_path`, including:
  - `assets/0fd4012bb82036d1/simplifynext_hackathon/prefabs/asphalt_road_202608270155_usda`
  - `assets/0fd4012bb82036d1/simplifynext_hackathon/prefabs/traffic_light_202608270102_usda`
  - `assets/e071469a36d3c8aa/vln_presentation/waic/prop/cardbox/cardbox_02_static`
- OrcaStudio stores those prefab payloads in its managed asset service rather than as standalone local USD files. Importing `street.json` resolves them by the paths above.
- `previews/` contains the locally cached previews available on this machine; these are reference images, not substitute USD prefab payloads.
