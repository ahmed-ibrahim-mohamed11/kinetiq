import sys
import time
from edge_ai.camera.manager import CentralCameraManager

mgr = CentralCameraManager.get_instance()
mgr.register_camera('cam-test', url='webcam://0', name='Test')
mgr.start_camera('cam-test', 'consumer-1')
for i in range(5):
    time.sleep(1.0)
    st = mgr.get_camera_status('cam-test')
    dev = mgr._get_or_create_device('cam-test')
    print(f'Sec {i+1}: state={st.get("state")}, frame_fps={st.get("frame_fps")}, frames_received={dev.frames_received}')
mgr.stop_camera('cam-test', 'consumer-1')

