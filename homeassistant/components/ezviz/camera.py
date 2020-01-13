"""This component provides basic support for Ezviz IP cameras."""
import asyncio
import logging

# from pyezviz.client import EzvizClient, EzvizCamera, PyEzvizError
# from pyezviz import EzvizClient, EzvizCamera, PyEzvizError
# from pyezviz.client import PyEzvizError
from haffmpeg.tools import IMAGE_JPEG, ImageFrame
from pyezviz.camera import EzvizCamera
from pyezviz.client import EzvizClient, PyEzvizError
import voluptuous as vol

from homeassistant.components.camera import (
    CAMERA_SERVICE_SCHEMA,
    PLATFORM_SCHEMA,
    SUPPORT_STREAM,
    Camera,
)
from homeassistant.components.camera.const import DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.service import async_extract_entity_ids

_LOGGER = logging.getLogger(__name__)

CONF_CAMERAS = "cameras"
CONF_SERIAL = "serial"
CONF_DEFAULT_CAMERA_USERNAME = "admin"

DEFAULT_RTSP_PORT = "554"

DATA_FFMPEG = "ffmpeg"

ATTR_DIRECTION = "direction"
ATTR_SPEED = "speed"
DEFAULT_SPEED = 5

ATTR_ENABLE = "enable"
ATTR_SWITCH = "switch"
AUDIO = "audio"
PRIVACY = "privacy"
STATE = "state"
FOLLOW_MOVE = "follow_move"

DIR_UP = "up"
DIR_DOWN = "down"
DIR_LEFT = "left"
DIR_RIGHT = "right"

SERVICE_PTZ = "ezviz_ptz"

EZVIZ_DATA = "ezviz"
ENTITIES = "entities"

CAMERAS_CONFIG = vol.Schema(
    {
        vol.Optional(CONF_USERNAME, default=CONF_DEFAULT_CAMERA_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SERIAL): cv.string,
    }
)


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_CAMERAS, default={}): vol.All(),
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
    }
)

SERVICE_PTZ_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_DIRECTION): vol.In([DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT]),
        vol.Optional(ATTR_SPEED, default=DEFAULT_SPEED): cv.positive_int,
    }
)


def setup_platform(hass, config, add_entities, disc_info=None):
    """Set up the Ezviz IP Cameras."""

    async def async_handle_ptz(service):
        """Handle PTZ service call."""
        direction = service.data.get(ATTR_DIRECTION, None)
        speed = service.data.get(ATTR_SPEED, None)

        _LOGGER.debug("HASS DATA: %s", hass.data["ezviz"])

        all_cameras = hass.data[EZVIZ_DATA][ENTITIES]
        entity_ids = await async_extract_entity_ids(hass, service)
        target_cameras = []
        if not entity_ids:
            target_cameras = all_cameras
        else:
            target_cameras = [
                camera for camera in all_cameras if camera.entity_id in entity_ids
            ]
        for camera in target_cameras:
            await camera.async_perform_ptz(direction, speed)

    async def async_switch_handler(call):
        """Handle switch call."""
        service = call.service
        entity_id = call.data["entity_id"][0]
        async_dispatcher_send(hass, f"{service}_{entity_id}")

    hass.services.async_register(
        DOMAIN, SERVICE_PTZ, async_handle_ptz, schema=SERVICE_PTZ_SCHEMA
    )

    hass.services.async_register(
        DOMAIN, "ezviz_switch_audio_on", async_switch_handler, CAMERA_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "ezviz_switch_audio_off", async_switch_handler, CAMERA_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "ezviz_switch_privacy_on", async_switch_handler, CAMERA_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "ezviz_switch_privacy_off", async_switch_handler, CAMERA_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "ezviz_switch_state_on", async_switch_handler, CAMERA_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "ezviz_switch_state_off", async_switch_handler, CAMERA_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "ezviz_switch_follow_move_on",
        async_switch_handler,
        CAMERA_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "ezviz_switch_follow_move_off",
        async_switch_handler,
        CAMERA_SERVICE_SCHEMA,
    )

    conf_cameras = config[CONF_CAMERAS]

    account = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]

    try:
        ezviz_client = EzvizClient(account, password)
        ezviz_client.login()
        cameras = ezviz_client.load_cameras()

    except PyEzvizError as exp:
        _LOGGER.error(exp)
        return None

    # now, let's build the HASS devices
    camera_entities = []

    # Add the cameras as devices in HASS
    for camera in cameras:

        camera_username = CONF_DEFAULT_CAMERA_USERNAME
        camera_password = ""
        camera_rtsp_stream = ""
        camera_serial = camera["serial"]

        # There seem to be a bug related to localRtspPort in Ezviz API...
        local_rtsp_port = DEFAULT_RTSP_PORT
        if camera["local_rtsp_port"] and camera["local_rtsp_port"] != 0:
            local_rtsp_port = camera["local_rtsp_port"]

        if camera_serial in conf_cameras:
            camera_username = conf_cameras[camera_serial]["username"]
            camera_password = conf_cameras[camera_serial]["password"]
            camera_rtsp_stream = "rtsp://{}:{}@{}:{}".format(
                camera_username, camera_password, camera["local_ip"], local_rtsp_port,
            )
        else:
            _LOGGER.info(
                "I found a camera (%s) but it is not configured. Please configure it if you wish to see the appropriate stream. Conf cameras: %s",
                camera_serial,
                conf_cameras,
            )

        camera["username"] = camera_username
        camera["password"] = camera_password
        camera["rtsp_stream"] = camera_rtsp_stream

        camera["ezviz_camera"] = EzvizCamera(ezviz_client, camera_serial)

        camera_entities.append(HassEzvizCamera(**camera))

    add_entities(camera_entities)


class HassEzvizCamera(Camera):
    """An implementation of a Foscam IP camera."""

    def __init__(self, **data):
        """Initialize an Ezviz camera."""
        super().__init__()

        self._username = data["username"]
        self._password = data["password"]
        self._rtsp_stream = data["rtsp_stream"]

        self._ezviz_camera = data["ezviz_camera"]
        self._serial = data["serial"]
        self._name = data["name"]
        self._status = data["status"]
        self._privacy = data["privacy"]
        self._audio = data["audio"]
        self._ir_led = data["ir_led"]
        self._state_led = data["state_led"]
        self._follow_move = data["follow_move"]
        self._alarm_notify = data["alarm_notify"]
        self._alarm_sound_mod = data["alarm_sound_mod"]
        self._encrypted = data["encrypted"]
        self._local_ip = data["local_ip"]
        self._detection_sensibility = data["detection_sensibility"]
        self._device_sub_category = data["device_sub_category"]
        self._local_rtsp_port = data["local_rtsp_port"]

        self._ffmpeg = None

    def update(self):
        """Update the camera states."""

        data = self._ezviz_camera.status()

        self._name = data["name"]
        self._status = data["status"]
        self._privacy = data["privacy"]
        self._audio = data["audio"]
        self._ir_led = data["ir_led"]
        self._state_led = data["state_led"]
        self._follow_move = data["follow_move"]
        self._alarm_notify = data["alarm_notify"]
        self._alarm_sound_mod = data["alarm_sound_mod"]
        self._encrypted = data["encrypted"]
        self._local_ip = data["local_ip"]
        self._detection_sensibility = data["detection_sensibility"]
        self._device_sub_category = data["device_sub_category"]
        self._local_rtsp_port = data["local_rtsp_port"]

    async def async_added_to_hass(self):
        """Subscribe to ffmpeg and add camera to list."""
        self._ffmpeg = self.hass.data[DATA_FFMPEG]
        entities = self.hass.data.setdefault(EZVIZ_DATA, {}).setdefault(ENTITIES, [])
        entities.append(self)

        # Other Entity method overrides

        _LOGGER.debug("Registering services for entity_id=%s", self.entity_id)
        async_dispatcher_connect(
            self.hass, f"ezviz_switch_ir_on_{self.entity_id}", self.switch_ir_on
        )
        async_dispatcher_connect(
            self.hass, f"ezviz_switch_ir_off_{self.entity_id}", self.switch_ir_off
        )
        async_dispatcher_connect(
            self.hass, f"ezviz_switch_audio_on_{self.entity_id}", self.switch_audio_on
        )
        async_dispatcher_connect(
            self.hass, f"ezviz_switch_audio_off_{self.entity_id}", self.switch_audio_off
        )
        async_dispatcher_connect(
            self.hass,
            f"ezviz_switch_privacy_on_{self.entity_id}",
            self.switch_privacy_on,
        )
        async_dispatcher_connect(
            self.hass,
            f"ezviz_switch_privacy_off_{self.entity_id}",
            self.switch_privacy_off,
        )
        async_dispatcher_connect(
            self.hass, f"ezviz_switch_state_on_{self.entity_id}", self.switch_state_on
        )
        async_dispatcher_connect(
            self.hass, f"ezviz_switch_state_off_{self.entity_id}", self.switch_state_off
        )
        async_dispatcher_connect(
            self.hass,
            f"ezviz_switch_follow_move_on_{self.entity_id}",
            self.switch_follow_move_on,
        )
        async_dispatcher_connect(
            self.hass,
            f"ezviz_switch_follow_move_off_{self.entity_id}",
            self.switch_follow_move_off,
        )

    async def async_perform_ptz(self, direction, sleep):
        """Perform a PTZ action on the camera."""
        await self.hass.async_add_executor_job(
            self._ezviz_camera.move, direction, sleep
        )

    @property
    def should_poll(self) -> bool:
        """Return True if entity has to be polled for state.

        False if entity pushes its state to HA.
        """
        return True

    @property
    def device_state_attributes(self):
        """Return the Ezviz-specific camera state attributes."""
        return {
            "serial": self._serial,
            "name": self._name,
            "status": self._status,
            "device_sub_category": self._device_sub_category,
            "privacy": self._privacy,
            "audio": self._audio,
            "ir_led": self._ir_led,
            "state_led": self._state_led,
            "follow_move": self._follow_move,
            "alarm_notify": self._alarm_notify,
            "alarm_sound_mod": self._alarm_sound_mod,
            "encrypted": self._encrypted,
            "local_ip": self._local_ip,
            "detection_sensibility": self._detection_sensibility,
        }

    @property
    def available(self):
        """Return True if entity is available."""
        return self._status

    @property
    def brand(self):
        """Return the camera brand."""
        return "Ezviz"

    @property
    def supported_features(self):
        """Return supported features."""
        if self._rtsp_stream:
            return SUPPORT_STREAM
        return 0

    @property
    def model(self):
        """Return the camera model."""
        return self._device_sub_category

    @property
    def is_on(self):
        """Return true if on."""
        return self._status

    @property
    def name(self):
        """Return the name of this camera."""
        return self._name

    async def async_camera_image(self):
        """Return a frame from the camera stream."""
        ffmpeg = ImageFrame(self._ffmpeg.binary, loop=self.hass.loop)

        image = await asyncio.shield(
            ffmpeg.get_image(self._rtsp_stream, output_format=IMAGE_JPEG,)
        )
        return image

    async def stream_source(self):
        """Return the stream source."""
        if self._local_rtsp_port:
            rtsp_stream_source = "rtsp://{}:{}@{}:{}".format(
                self._username, self._password, self._local_ip, self._local_rtsp_port
            )
            _LOGGER.debug(
                "Camera %s source stream: %s", self._serial, rtsp_stream_source
            )
            self._rtsp_stream = rtsp_stream_source
            return rtsp_stream_source
        return None

    def switch_audio_on(self):
        """Switch audio on."""
        self._switch("audio", 1)

    def switch_audio_off(self):
        """Switch audio on."""
        self._switch("audio", 0)

    def switch_ir_on(self):
        """Switch IR on."""
        self._switch("ir", 1)

    def switch_ir_off(self):
        """Switch IR on."""
        self._switch("ir", 0)

    def switch_privacy_on(self):
        """Switch privacy on."""
        self._switch("privacy", 1)

    def switch_privacy_off(self):
        """Switch privacy on."""
        self._switch("privacy", 0)

    def switch_state_on(self):
        """Switch state on."""
        self._switch("state", 1)

    def switch_state_off(self):
        """Switch state on."""
        self._switch("state", 0)

    def switch_follow_move_on(self):
        """Switch follow_move on."""
        self._switch("follow_move", 1)

    def switch_follow_move_off(self):
        """Switch follow_move on."""
        self._switch("follow_move", 0)

    def _switch(self, switch, enable):
        """Switch switch named switch to enable state."""
        _LOGGER.debug(
            "Switch %s for the camera %s to state: %s", switch, self._name, enable
        )

        if switch == "ir":
            self._ezviz_camera.switch_device_ir_led(enable)
        elif switch == "state":
            self._ezviz_camera.switch_device_state_led(enable)
        elif switch == "audio":
            self._ezviz_camera.switch_device_audio(enable)
        elif switch == "privacy":
            self._ezviz_camera.switch_privacy_mode(enable)
        elif switch == "follow_move":
            self._ezviz_camera.switch_follow_move(enable)
        else:
            return None
        return True