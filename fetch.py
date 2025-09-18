#!/usr/bin/python3
"""
fetch.py

Fetches and deprecates images defined in input.json
For more details refer to README.md


"""
from datetime import datetime
import sys
import os
import json
import hashlib
import logging
from logging.handlers import SysLogHandler
import yaml
import requests
import openstack



class ImgBuildLogger:
    """
    Set value for exitting the program and handle logging
    0 = OK
    1 = Warning
    2 = Error
    """

    def __init__(self, **kwargs):
        ''' Initialize log object '''
        self.config = kwargs
        self._code = 0
        self._log = logging.getLogger("__project_codename__")
        self._log.setLevel(logging.DEBUG)

        sysloghandler = SysLogHandler()
        sysloghandler.setLevel(logging.DEBUG)
        self._log.addHandler(sysloghandler)

        streamhandler = logging.StreamHandler(sys.stdout)
        streamhandler.setLevel(
            logging.getLevelName(self.config.get("debug_level", 'INFO'))
        )
        self._log.addHandler(streamhandler)

        if 'log_file' in self.config:
            log_file = self.config['log_file']
        else:
            home_folder = os.environ.get(
                'HOME', os.environ.get('USERPROFILE', '')
            )
            log_folder = os.path.join(home_folder, "log")
            log_file = os.path.join(log_folder, "__project_codename__.log")

        if not os.path.exists(os.path.dirname(log_file)):
            os.mkdir(os.path.dirname(log_file))

        filehandler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=102400000
        )
        # create formatter
        formatter = logging.Formatter(
            '%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
        )
        filehandler.setFormatter(formatter)
        filehandler.setLevel(logging.DEBUG)
        self._log.addHandler(filehandler)
        return True

    def _output(self, message):
        if self.config['output_format'] == 'JSON':
            return json.dumps(message, indent=2)
        elif self.config['output_format'] == 'YAML':
            return yaml.dump(message, Dumper=yaml.Dumper)
        elif self.config['output_format'] == 'PLAIN':
            return f"{message}"
        else:
            self._log.warning(
                "Output format '%s' not supported",
                self.config['output_format']
            )
            return message

    def info(self, message):
        return self._log.info(self._output(message))

    def warning(self, message):
        self._code = max(self._code, 1)
        return self._log.warning(self._output(message))

    def error(self, message):
        self._code = 2
        return self._log.error(self._output(message))

    def debug(self, message):
        return self._log.debug(self._output(message))


    @property
    def exit_code(self) -> int:
        """
        Return the set code
        Returns
        -------
        int
            Current exit code
        """
        return self._code


cloud = os.getenv("IMAGEBUILDER_CLOUD") # get it once to avoid potential race conditions
network = os.getenv("IMAGEBUILDER_NETWORK")

logger = ImgBuildLogger(
    config={
        'log_file': os.getenv(
            "IMAGEBUILDER_LOG_FILE",
            f"./{cloud}_log.json"
        )
    }
)


def get_file_hash(filename: str, cur_hash: hashlib._hashlib.HASH) -> str:
    """
    Calculate the hash of a file chunked
    Returns the hexdigest in lowercase

    Returns
    -------
    str
        Hash of filename in the format of cur_hash
        Empty string if file does not exist
    """

    if not os.path.exists(filename):
        return ""

    with open(filename, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b''):
            cur_hash.update(chunk)

    return cur_hash.hexdigest().lower()



def download_image(url: str, filename: str, new_checksum: str) -> bool:
    """
    Downloads a file from a specified url using chucking
    Also converts it to a .raw file

    Returns
    -------
    bool
        True if everything is successful
        False if something goes wrong
    """

    print_progressbar = sys.stdout.isatty()

    # Check if the same file already exists on disk
    cur_hash = get_file_hash("tmp/"+filename, hashlib.sha256())
    if cur_hash != "" and cur_hash in new_checksum.lower():

        logger.info(f"The newest version of {filename} already exists on disk.")

        # Local file exists but raw doesn't, create one
        if not os.path.exists("tmp/"+filename+".raw"):
            logger.info("RAW file did not exist. Creating...")
            os.system(f"qemu-img convert {'-p' if print_progressbar else ''} \
                      -O raw tmp/{filename} tmp/{filename}.raw")

        return True


    # Remove old files that may exist before downloading new ones
    cleanup_files(filename)



    try:
        with requests.get(
                url,
                allow_redirects=True,
                timeout=600,
                stream=True
            ) as r:
            with open("tmp/"+filename,"wb") as f:
                file_size = int(r.headers.get("content-length"))
                progress = 0

                if print_progressbar:
                    disp_filename = filename[0:(int(os.get_terminal_size().columns*0.5)-1)]
                    if disp_filename != filename:
                        disp_filename = disp_filename[:-3] + "..."

                    bar_width = min(50,
                                    int(os.get_terminal_size().columns - len(disp_filename) - 3))

                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress += len(chunk)
                    if print_progressbar:
                        done = int(bar_width * progress / file_size)
                        sys.stdout.write(
                            f"\r{disp_filename}\t[{'=' * done}{' ' * (bar_width-done)}]"
                            )
                        sys.stdout.flush()

            if print_progressbar:
                sys.stdout.write("\n")
                sys.stdout.flush()


        new_hash = get_file_hash("tmp/"+filename, hashlib.sha256())
        if (new_hash != "" and new_hash not in new_checksum.lower()) and new_checksum != filename:
            logger.error(f"Checksum of {filename} does not match the one downloaded!")
            cleanup_files(filename)
            return False

        logger.info(f"Converting {filename} to .raw")

        # Convert to raw
        os.system(f"qemu-img convert {'-p' if print_progressbar else ''}\
                   -O raw tmp/{filename} tmp/{filename}.raw")

        return True

    except requests.exceptions.HTTPError:
        logger.error(f"HTTP error while downloading {filename}")

    except requests.exceptions.ReadTimeout:
        logger.error(f"Download for {filename} timed out")

    except requests.exceptions.ConnectionError:
        logger.error(f"Unable to download {filename}")


    return False




def validate_raw_checksum(conn: openstack.connection.Connection,
                          filename: str, version: any) -> bool:
    """
    Compares the currently existing raw file's md5 checksum to the one on openstack.
    Returns true if they match

    Returns
    -------
    bool
        True if the MD5 hash of the local raw file is the same as the current one on openstack
        False otherwise
    """
    checksum_matches = False

    images = list(conn.image.images(
                            name=version["image_name"],
                            owner=conn.current_project_id,
                            visibility=version["visibility"]
                    ))

    for cur_img in images:
        if cur_img.checksum.lower() == get_file_hash("tmp/"+filename+".raw", hashlib.md5()):
            checksum_matches = True


    if len(images) > 1:
        logger.warning(f"More than 1 image for {version['image_name']} already exists")


    return checksum_matches



def validate_checksum(version: any, filename: str,
                      conn: openstack.connection.Connection, cloud: str) -> str | None:
    """
    Validates checksums to check if an image requires updating

    Returns
    -------
    str
        A new checksum that's fetched online from version.checksum_url
        If checksum_url is not specified within version, the filename will be returned

    None
        If any critical error occures None is returned.
    """

    old_checksum = None
    if os.path.exists(f"checksums/{cloud}_{version['image_name'].replace(' ', '_')}_CHECKSUM"):
        with open(f"checksums/{cloud}_{version['image_name'].replace(' ', '_')}_CHECKSUM",
                  "r", encoding="utf-8") as f:
            old_checksum = f.read()
    elif version.get("checksum_url"):
        logger.info(
            f"Checksum file for {cloud}_{version['image_name']} does not exist. Creating one"
            )


    if not version.get("checksum_url"):
        logger.info(
            f"Checksum URL not specified for {version['image_name']}. Skipping checksum fetching..."
            )
        return filename


    error = False
    new_checksum = old_checksum
    try:
        new_checksum = next((
                line
                for line in requests.get(version["checksum_url"], timeout=5).text.split('\n')
                    if filename in line and not line.startswith("#")
            ), None)

        if new_checksum is None:
            logger.error("New checksum could not be found in the list")
            new_checksum = old_checksum


    except requests.exceptions.HTTPError:
        error = True
        logger.error(f"HTTP error while downloading checksum for {version['image_name']}")

    except requests.exceptions.ReadTimeout:
        error = True
        logger.error(f"Fetching the checksum for {version['image_name']} timed out")

    except requests.exceptions.ConnectionError:
        error = True
        logger.error(f"Unable to fetch new checksum for {version['image_name']}")


    if error:
        logger.error(f"An error occured validating {version['image_name']}")
        return None

    if new_checksum == old_checksum and validate_raw_checksum(conn, filename, version):

        current_images = list(conn.image.images(
                name=version["image_name"], owner=conn.current_project_id,
                visibility=version["visibility"],
                sort_key='created_at',
                sort_dir='desc'
        ))

        current_img_id = current_images[0].id if current_images else None

        if len(current_images) > 1:
            logger.warning(f"More than 1 image for {version['image_name']} already exists")
        else:

            logger.info(f"{version['image_name']} already up to date")
            # Delete unused if they exist
            delete_unused_images(conn, version["image_name"],current_img_id)

        return None



    return new_checksum


def test_image_pinging(conn: openstack.connection.Connection, server_id: int) -> bool:
    """
    Test if the newly built image can be pinged by attaching it to a network
    Creates a router, interface and a floating ip which get cleaned up

    Returns
    -------
    bool
        True if the test server can be pinged properly
        False if an error is detected
    """

    if os.getenv("IMAGEBUILDER_DISABLE_PINGING") is not None:
        logger.info("Skipping ping test...")
        return True

    logger.info("Testing pinging")

    public_id = conn.network.find_network("public", is_router_external=True).id

    try:
        floating_ip = conn.network.create_ip(
            floating_network_id=public_id
        )
    except openstack.exceptions.ConflictException as e:
        logger.error("Creation of floating ip failed")
        logger.error(str(e))
        return False

    port = next(conn.network.ports(device_id=server_id), None)

    if port is None:
        logger.error("No network port found")
        conn.network.delete_ip(floating_ip)
        return False

    floating_ip = conn.network.update_ip(
        floating_ip.id,
        port_id=port.id
    )


    ping_result = os.system(f"timeout 360 bash -c \
                            'while ! \
                                 ping -c 1 -W 1 {floating_ip.floating_ip_address} \
                                 > /dev/null 2>&1; \
                                 do sleep 1; \
                             done'"
                            )


    conn.network.delete_ip(floating_ip)

    if ping_result == 0:
        logger.info("Ping tests ok!")
        return True


    return False



def test_image(conn: openstack.connection.Connection, image: any, network: str) -> bool:
    """
    Test the newly created image by creating a test server and pinging it
    Returns
    -------
    bool
        True if the server can be created and pinged
        False if errors are detected
    """

    logger.info("Testing newly created image")

    secgroup = conn.network.find_security_group("IMAGEBUILDER_PING_TEST",
                                                project_id=conn.current_project.id)

    if secgroup is None:
        secgroup = conn.network.create_security_group(name="IMAGEBUILDER_PING_TEST")
        conn.network.create_security_group_rule(
            security_group_id=secgroup.id,
            direction='ingress',
            ether_type='IPv4',
            protocol='icmp'
        )
        conn.network.create_security_group_rule(
            security_group_id=secgroup.id,
            direction='egress',
            ether_type='IPv4',
            protocol='icmp'
        )

    try:
        test_server = conn.create_server(
            name=image.name+"_TESTSERVER",
            image=image,
            flavor="standard.tiny",
            wait=True,
            auto_ip=False,
            network=network,
            timeout=360,
            security_groups=[secgroup.id],
        )
    except openstack.exceptions.SDKException as e:
        logger.error("Server failed to be created!")
        logger.error(str(e))
        return False

    logger.info(f"Server with id ({test_server.id}) created")


    found_test_server = conn.get_server_by_id(test_server.id)


    if found_test_server["status"] == "ERROR":
        conn.delete_server(test_server.id)
        logger.error(f"Server {test_server.id} failed to start with image {image.id}!")
        return False

    # Test pinging
    ping_result = test_image_pinging(conn, test_server.id)


    conn.delete_server(test_server.id, wait=True)


    if not ping_result:
        logger.error(f"Server {test_server.id} failed to respond to ping!")
        return False

    logger.info("All tests ok!")

    return True


def cleanup_files(filename: str) -> None:
    """
    Delete image files that the program creates.
    """

    try:
        os.remove("tmp/"+filename)
    except OSError:
        pass

    try:
        os.remove("tmp/"+filename+".raw")
    except OSError:
        pass




def create_image(conn: openstack.connection.Connection, version: any,
                 filename: str, new_checksum: str, network: str) -> any:
    """
    Creates an image

    Returns
    -------
    any:
        If everything is successful the newly created image is returned.
        If the image is already up to date or if there's an error None is returned
    """

    # Download image
    if not download_image(version["image_url"],filename, new_checksum):
        cleanup_files(filename)
        return None

    # Image downloaded, but is it the same as the current image on openstack?
    logger.info("Comparing downloaded image against openstack")

    if validate_raw_checksum(conn, filename, version):

        # Everything is ok so write the checksum pre-emptively
        if new_checksum != filename:

            img_name = version['image_name'].replace(' ', '_')
            with open(
                    f"checksums/{conn.config.name}_{img_name}_CHECKSUM",
                    "w", encoding="utf-8"
                    ) as f:
                f.write(new_checksum)


        logger.info(
            f"Openstack already has the current version of {version['image_name']}"
        )

        current_img_id = next(
            conn.image.images(
                name=version["image_name"], owner=conn.current_project_id,
                visibility=version["visibility"]
                )
            , None).id


        delete_unused_images(conn, version["image_name"],current_img_id)
        return None

    logger.info("Uploading image")

    properties = version.get("properties", {})
    properties["description"] = "To find out which user to login with: ssh in as root."
    properties.setdefault("os_type", "linux") # configures ephemeral drive formatting
    properties.setdefault("hw_vif_multiqueue_enabled", True) # set multiqueue to on by default

    new_image = conn.create_image(
        name=version["image_name"],
        filename="tmp/"+filename+".raw",
        wait=True,
        visibility="private",
        allow_duplicates=True,
        disk_format="raw",
        container_format="bare",
        properties=properties
    )

    if new_image is None:
        logger.error("An error occured while creating the image")
        return None

    logger.info(f"Image with id ({new_image.id}) created")


    # Test image
    if not test_image(conn, new_image, network):
        return None




    return new_image




def delete_unused_images(conn: openstack.connection.Connection,
                         name: str, skip: str = None) -> bool:
    """
    Delete unused images but skip specified image if provided

    Returns
    -------
    bool
        True if at least one old version of the image is still used by a server or a volume
        False if the image was fully deleted
    """

    still_using = False
    # Loop over existing images
    for img in conn.image.images(name=name, owner=conn.current_project_id):
        if img.id == skip:
            continue

        # Check if image is unused
        if (next(conn.compute.servers(image=img.id, all_projects=True), None) is None and
            next(conn.block_storage.volumes(image_id=img.id, all_projects=True),None) is None):
            # Not used by any server nor volume
            logger.info(f"Deleting image {img.id}")
            conn.delete_image(img.id)
        elif img.visibility != "community":
            # used by someone. set to community
            logger.info(f"Setting image {img.id} to community")
            conn.image.update_image(img.id, visibility="community")
            still_using = True



    return still_using










def main() -> None:
    """
    Reads defined images from input.json, downloads new images and deprecates old ones
    """

    if cloud is None or network is None:

        missing = [
            x for x in ["IMAGEBUILDER_CLOUD", "IMAGEBUILDER_NETWORK"]
            if os.getenv(x) is None
            ]

        raise EnvironmentError(f"Environtment variables are not set! ({', '.join(missing)})")


    openstack.enable_logging(debug=(
        os.getenv("IMAGEBUILDER_DEBUG") is not None
        ))

    conn = openstack.connect(cloud=cloud)

    # Load file from argv
    input_data = None
    input_file = os.getenv("IMAGEBUILDER_INPUT_FILE", "input.json")

    with open(input_file, "r", encoding="utf-8") as f:
        input_data = json.load(f)


    for version in input_data["current"]:
        filename = version["image_url"].split("/")[-1]

        new_checksum = validate_checksum(version, filename, conn, cloud)

        if new_checksum is None:
            continue



        logger.info(f"Downloading {version['image_name']}")

        new_image = create_image(conn, version, filename, new_checksum, network)

        if new_image is None:
            continue



        # Everything is ok! Set visibility to what it should be
        logger.info(f"Setting image to {version['visibility']}")
        conn.image.update_image(new_image.id, visibility=version["visibility"])



        # Remove old ones
        delete_unused_images(conn, version["image_name"], new_image.id)

        if new_checksum != filename:
            with open(f"checksums/{cloud}_{version['image_name'].replace(' ', '_')}_CHECKSUM",
                    "w",encoding="utf-8") as f:
                f.write(new_checksum)

        logger.info(f"{version['image_name']} has been successfully updated")

    for version in input_data["deprecated"]:

        still_using = delete_unused_images(conn, version["image_name"])

        # It is deprecated so get rid of the files on disk
        try:
            os.remove(f"checksums/{cloud}_{version['image_name'].replace(' ', '_')}_CHECKSUM")
        except OSError:
            pass

        if not still_using:
            logger.info(f"{version['image_name']} removed completely")
        else:
            logger.info(
                f"{version['image_name']} is still used by someone so it is not fully removed"
            )

        if version.get("filename"):
            cleanup_files(version["filename"])







if __name__=="__main__":
    main()
    sys.exit(logger.exit_code)
