import torch
import orcalab
from orcalab.scene import Scene
from orcalab.assets import RobotAsset

# 1. Force PyTorch to use CPU (since there is no NVIDIA GPU)
device = torch.device("cpu")
print(f"Loading NaViLa on device: {device}")

# 2. Initialize OrcaLab Simulator
sim = orcalab.Simulator(headless=False)
scene = Scene()

# 3. Add ground plane
scene.add_ground_plane()

# 4. Define and load the NaViLa Dog Asset
# (Replace with the actual path to your Go2 / A1 .urdf or .usd file)
dog_path = "assets/robots/go2/go2.urdf" 

navila_dog = RobotAsset(
    name="navila_dog",
    file_path=dog_path,
    pos=[0.0, 0.0, 0.45]  # Spawns slightly above the ground plane
)

# 5. Add robot to scene & start simulation
scene.add_robot(navila_dog)
sim.load_scene(scene)

# 6. Main simulation loop
print("Simulation running. Close the simulator window to stop.")
while sim.is_running():
    sim.step()
