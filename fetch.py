#!/usr/bin/python3
"""
fetch.py

Fetches and deprecates images defined in input.json
For more details refer to README.md


"""
import sys
import os
import json
import hashlib
import requests
import openstack



def get_file_hash(filename: str, cur_hash: hashlib._hashlib.HASH) -> str:
    """
    Calculate the hash of a file chunked
    Returns the hexdigest in lowercase
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
    """

    print_progressbar = sys.stdout.isatty()

    # Check if the same file already exists on disk
    cur_hash = get_file_hash("tmp/"+filename, hashlib.sha256())
    if cur_hash != "" and cur_hash in new_checksum.lower():

        print("File already exists on disk.")

        # Local file exists but raw doesn't, create one
        if not os.path.exists("tmp/"+filename+".raw"):
            print("RAW file did not exist. Creating...")
            os.system(f"qemu-img convert {"-p" if print_progressbar else ''} \
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

                disp_filename = filename[0:(int(os.get_terminal_size().columns*0.5)-1)]
                if disp_filename != filename:
                    disp_filename = disp_filename[:-3] + "..."

                bar_width = min(50, int(os.get_terminal_size().columns - len(disp_filename) - 3))

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
        if new_hash != "" and new_hash not in new_checksum.lower():
            print("Checksum of the new file does not match!")
            cleanup_files(filename)
            return False

        print("Converting to .raw")

        # Convert to raw
        os.system(f"qemu-img convert {"-p" if print_progressbar else ''}\
                   -O raw tmp/{filename} tmp/{filename}.raw")

        return True

    except requests.exceptions.HTTPError:
        print(f"HTTP error while downloading {filename}")

    except requests.exceptions.ReadTimeout:
        print(f"Download for {filename} timed out")

    except requests.exceptions.ConnectionError:
        print(f"Unable to download {filename}")


    return False




def validate_raw_checksum(conn: openstack.connection.Connection,
                          filename: str, version: any) -> bool:
    """
    Compares the currently existing raw file's md5 checksum to the one on openstack.
    Returns true if they match
    """
    cur_img = next(conn.image.images(
                            name=version["image_name"],
                            owner=conn.current_project_id,
                            visibility=version["visibility"]
                    ), None)


    if (cur_img is not None and
        cur_img.checksum.lower() == get_file_hash("tmp/"+filename+".raw", hashlib.md5())):
        return True


    return False



def validate_checksum(version: any, filename: str,
                      conn: openstack.connection.Connection, cloud: str) -> str | None:
    """
    Validates checksums to check if an image requires updating
    """

    old_checksum = None
    if os.path.exists("checksums/"+cloud+"_"+version["image_name"]+"_CHECKSUM"):
        with open("checksums/"+cloud+"_"+version["image_name"]+"_CHECKSUM",
                  "r", encoding="utf-8") as f:
            old_checksum = f.read()
    else:
        print(f"Checksum file for {cloud}_{version['image_name']} does not exist. Creating one")


    if os.getenv("IMAGEBUILDER_SKIP_CHECKSUM") is not None and old_checksum is not None:
        print("Skipping checksum fetching...")
        return old_checksum # Should something else be returned?



    error = False
    new_checksum = old_checksum
    try:
        new_checksum = next((
                line
                for line in requests.get(version["checksum_url"], timeout=5).text.split('\n')
                    if filename in line and not line.startswith("#")
            ), None)

        if new_checksum is None:
            print("New checksum could not be found in the list")
            new_checksum = old_checksum


    except requests.exceptions.HTTPError:
        error = True
        print(f"HTTP error while downloading checksum for {version['image_name']}")

    except requests.exceptions.ReadTimeout:
        error = True
        print(f"Fetching the checksum for {version['image_name']} timed out")

    except requests.exceptions.ConnectionError:
        error = True
        print(f"Unable to fetch new checksum for {version['image_name']}")


    if error:
        print(f"An error occured validating {version['image_name']}")
        return None

    if new_checksum == old_checksum and validate_raw_checksum(conn, filename, version):
        print(f"{version['image_name']} already up to date")
        return None



    return new_checksum


def test_image_pinging(conn: openstack.connection.Connection, server_id: int) -> bool:
    """
    Test if the newly built image can be pinged by attaching it to a network
    Creates a router, interface and a floating ip which get cleaned up
    """

    if os.getenv("IMAGEBUILDER_DISABLE_PINGING") is not None:
        print("Skipping ping test...")
        return True

    print("Testing pinging")

    public_id = conn.network.find_network("public", is_router_external=True).id


    floating_ip = conn.network.create_ip(
        floating_network_id=public_id
    )

    port = next(conn.network.ports(device_id=server_id), None)

    if port is None:
        print("No network port found")
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
        print("Ping tests ok!")
        return True


    return False



def test_image(conn: openstack.connection.Connection, image: any, network: str) -> bool:
    """
    Test the newly created image by creating a test server and pinging it
    """

    print("Testing newly created image")

    secgroup = conn.network.find_security_group("IMAGEBUILDER_PING_TEST")

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

    print(f"Server with id ({test_server.id}) created")


    found_test_server = conn.get_server_by_id(test_server.id)


    if found_test_server["status"] == "ERROR":
        conn.delete_server(test_server.id)
        conn.network.delete_security_group(secgroup.id)
        print(f"Server {test_server.id} failed to start with image {image.id}!")
        return False

    # Test pinging
    ping_result = test_image_pinging(conn, test_server.id)


    conn.delete_server(test_server.id, wait=True)


    if not ping_result:
        print(f"Server {test_server.id} failed to respond to ping!")
        return False

    print("All tests ok!")

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
    """

    # Download image
    if not download_image(version["image_url"],filename, new_checksum):
        cleanup_files(filename)
        return None

    # Image downloaded, but is it the same as the current image on openstack?
    print("Comparing downloaded image against openstack")

    if validate_raw_checksum(conn, filename, version):

        # Everything is ok so write the checksum pre-emptively
        with open("checksums/"+conn.config.name+"_"+version["image_name"]+"_CHECKSUM","w",
                  encoding="utf-8") as f:
            f.write(new_checksum)


        print(f"Openstack already has the current version of {version['image_name']}")
        return None

    print("Uploading image")

    properties = version.get("properties", {})
    properties["descrption"] = "To find out which user to login with: ssh in as root."
    properties["os_distro"] = version["distro"]
    properties["os_type"] = version.get("os_type", "linux") # configures ephemeral drive formatting

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
        print("An error occured while creating the image")
        return None

    print(f"Image with id ({new_image.id}) created")


    # Test image
    if not test_image(conn, new_image, network):
        return None




    return new_image




def delete_unused_images(conn: openstack.connection.Connection,
                         name: str, skip: int = None) -> bool:
    """
    Delete unused images but skip specified image if provided
    """

    still_using = False
    # Loop over existing images
    for img in conn.image.images(name=name, owner=conn.current_project_id):
        if img.id == skip:
            continue

        # Check if image is unused
        if (next(conn.compute.servers(image=img.id, all_projects=True), None) is None and
            next(conn.block_storage.volumes(image_id=img.id, all_projects=True),None) is None):
            # not used by any server. good to delete.
            conn.delete_image(img.id)
        else:
            # used by someone. set to community
            conn.image.update_image(img.id, visibility="community")
            still_using = True



    return still_using






def main() -> None:
    """
    Reads defined images from input.json, downloads new images and deprecates old ones
    """

    cloud = os.getenv("IMAGEBUILDER_CLOUD") # get it once to avoid potential race conditions
    network = os.getenv("IMAGEBUILDER_NETWORK")

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

    input_data = None
    with open("input.json", "r", encoding="utf-8") as f:
        input_data = json.load(f)


    for version in input_data["current"]:
        filename = version["image_url"].split("/")[-1]

        print(f"=== {version["image_name"]} ===")

        new_checksum = validate_checksum(version, filename, conn, cloud)

        if new_checksum is None:
            print("")
            continue



        print(f"Downloading {version['image_name']}")

        new_image = create_image(conn, version, filename, new_checksum, network)

        if new_image is None:
            print("")
            continue



        # Everything is ok! Set visibility to what it should be
        print(f"Setting image to {version['visibility']}")
        conn.image.update_image(new_image.id, visibility=version["visibility"])



        # Remove old ones
        delete_unused_images(conn, version["image_name"], new_image.id)

        with open("checksums/"+cloud+"_"+version["image_name"]+"_CHECKSUM","w",
                  encoding="utf-8") as f:
            f.write(new_checksum)

        print("")


    for version in input_data["deprecated"]:

        print(f"=== {version["image_name"]} ===")

        still_using = delete_unused_images(conn, version["image_name"])

        # It is deprecated so get rid of the files on disk
        try:
            os.remove("checksums/"+cloud+"_"+version["image_name"]+"_CHECKSUM")
        except OSError:
            pass

        if not still_using:
            print(f"{version['image_name']} removed completely")
        else:
            print(f"{version['image_name']} is still used by someone so it is not fully removed")

        cleanup_files(version["filename"])

        print("")





if __name__=="__main__":
    main()
