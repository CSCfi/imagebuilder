#!/usr/bin/python3
"""
fetch.py

Fetches and deprecates images defined in input.json
For more details refer to README.md


"""
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



def download_file(url: str, filename: str, new_checksum: str) -> bool:
    """
    Downloads a file from a specified url using chucking
    Also converts it to a .raw file
    """

    # Check if the same file already exists on disk
    cur_hash = get_file_hash("tmp/"+filename, hashlib.sha256())
    if cur_hash != "" and cur_hash in new_checksum.lower():
        print("File already exists on disk.")
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
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Convert to raw
        os.system(f"qemu-img convert -O raw tmp/{filename} tmp/{filename}.raw")

        return True

    except requests.exceptions.HTTPError:
        print(f"HTTP error while downloading {filename}")

    except requests.exceptions.ReadTimeout:
        print(f"Download for {filename} timed out")

    except requests.exceptions.ConnectionError:
        print(f"Unable to download {filename}")


    return False







def validate_checksum(url: str, filename: str, image_name: str, cloud: str) -> str | None:
    """
    Validates checksums to check if an image requires updating
    """


    old_checksum = None
    if os.path.exists("checksums/"+cloud+"_"+image_name+"_CHECKSUM"):
        with open("checksums/"+cloud+"_"+image_name+"_CHECKSUM","r", encoding="utf-8") as f:
            old_checksum = f.read()


    error = False
    new_checksum = old_checksum
    try:
        new_checksum = next((
                line
                for line in requests.get(url, timeout=5).text.split('\n')
                    if filename in line and not line.startswith("#")
            ), old_checksum)

    except requests.exceptions.HTTPError:
        error = True
        print(f"HTTP error while downloading checksum for {image_name}")

    except requests.exceptions.ReadTimeout:
        error = True
        print(f"Fetching the checksum for {image_name} timed out")

    except requests.exceptions.ConnectionError:
        error = True
        print(f"Unable to fetch new checksum for {image_name}")




    if error:
        print(f"An error occured validating {image_name}")
        return None

    if new_checksum == old_checksum:
        print(f"{image_name} already up to date")
        return None



    return new_checksum


def test_image_pinging(conn: openstack.connection.Connection,
                       network: str, image: any, server_id: int) -> bool:
    """
    Test if the newly built image can be pinged by attaching it to a network
    Creates a router, interface and a floating ip which get cleaned up
    """

    if os.getenv("IMAGEBUILDER_DISABLE_PINGING") is not None:
        print("Skipping ping test...")
        return True

    print("Testing pinging")

    public_id = conn.network.find_network("public", is_router_external=True).id

    router = conn.network.find_router(f"{image.name}_TEST_ROUTER")

    if not router:
        router = conn.network.create_router(
            name=f"{image.name}_TEST_ROUTER",
            external_gateway_info={"network_id": public_id},
        )
    else:
        conn.network.update_router(
            router.id,
            external_gateway_info={"network_id": public_id}
        )

    subnet = next(conn.network.subnets(network_id=conn.network.find_network(network).id),None)

    if subnet is None:
        conn.network.update_router(router, external_gateway_info=None)
        conn.network.delete_router(router.id)
        print(f"No subnet is configured for {network}")
        return False

    attached = any(
        port.fixed_ips[0]['subnet_id'] == subnet.id
        for port in conn.network.ports(device_id=router.id)
        if port.fixed_ips
    )

    if attached:
        conn.network.remove_interface_from_router(router, subnet_id=subnet.id)

    conn.network.add_interface_to_router(
        router.id,
        subnet_id=subnet.id
    )

    floating_ip = conn.network.create_ip(
        floating_network_id=public_id
    )

    port = next(conn.network.ports(device_id=server_id), None)

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
    conn.network.remove_interface_from_router(router, subnet_id=subnet.id)
    conn.network.update_router(router, external_gateway_info=None)
    conn.network.delete_router(router.id)

    if ping_result == 0:
        print("Print tests ok!")


    return ping_result == 0



def test_image(conn: openstack.connection.Connection, image: any, network: str) -> bool:
    """
    Test the newly created image by creating a test server and pinging it
    """

    print("Testing newly created image")

    secgroup = conn.network.create_security_group(name=f"{image.name}_TEST_SECURITY_GROUP")
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
    ping_result = test_image_pinging(conn, network, image, test_server.id)


    conn.delete_server(test_server.id, wait=True)
    conn.network.delete_security_group(secgroup.id)


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
    if not download_file(version["image_url"],filename, new_checksum):
        cleanup_files(filename)
        return None

    # Image downloaded, but is it the same as the current image on openstack?
    cur_img = next(conn.image.images(
                            name=version["image_name"],
                            owner=conn.current_project_id,
                            visibility=version["visibility"]
                    ), None)


    if (cur_img is not None and
        cur_img.checksum.lower() == get_file_hash("tmp/"+filename+".raw", hashlib.md5())):

        # Everything is ok so write the checksum pre-emptively
        with open("checksums/"+conn.config.name+"_"+version["image_name"]+"_CHECKSUM","w",
                  encoding="utf-8") as f:
            f.write(new_checksum)


        print(f"Openstack already has the current version of {version['image_name']}")
        return None


    new_image = conn.create_image(
        name=version["image_name"],
        filename="tmp/"+filename+".raw",
        wait=True,
        visibility="private",
        allow_duplicates=True,
        disk_format="raw",
        container_format="bare",
        properties={
            "description":"To find out which user to login with: ssh in as root.",
            "os_distro":version["distro"]
        }
    )

    if new_image is None:
        print("An error occured while creating the image")
        return None

    print(f"Image with id ({new_image.id}) created")


    # Test image
    if not test_image(conn, new_image, network):
        return None




    return new_image




def main() -> None:
    """
    Reads defined images from input.json, downloads new images and deprecates old ones
    """

    cloud = os.getenv("IMAGEBUILDER_CLOUD") # get it once to avoid potential race conditions
    network = os.getenv("IMAGEBUILDER_NETWORK")


    openstack.enable_logging(debug=(
        os.getenv("IMAGEBUILDER_DEBUG") is not None
        ))
    conn = openstack.connect(cloud=cloud)

    input_data = None
    with open("input.json", "r", encoding="utf-8") as f:
        input_data = json.load(f)


    for version in input_data["current"]:
        filename = version["image_url"].split("/")[-1]

        new_checksum = validate_checksum(version["checksum_url"], filename,
                                         version["image_name"], cloud)

        if new_checksum is None:
            continue



        print(f"Downloading {version['image_name']}")

        new_image = create_image(conn, version, filename, new_checksum, network)

        if new_image is None:
            continue



        # Everything is ok! Set visibility to what it should be
        print(f"Setting image to {version["visibility"]}")
        conn.image.update_image(new_image.id, visibility=version["visibility"])



        # Remove old ones
        for img in conn.image.images(name=version["image_name"], owner=new_image.owner):
            if img.id == new_image.id:
                continue

            # Check if image is unused
            if (next(conn.compute.servers(image=img.id, all_projects=True), None) is None and
                next(conn.block_storage.volumes(image_id=img.id, all_projects=True),None) is None):
                # not used by any server. good to delete?
                conn.delete_image(img.id)
            else:
                # used by someone. set to community
                conn.image.update_image(img.id, visibility="community")


        with open("checksums/"+cloud+"_"+version["image_name"]+"_CHECKSUM","w",
                  encoding="utf-8") as f:
            f.write(new_checksum)


    for version in input_data["deprecated"]:
        # Loop over existing images
        for img in conn.image.images(name=version["image_name"], owner=conn.current_project_id):

            # Check if image is unused
            if (next(conn.compute.servers(image=img.id, all_projects=True), None) is None and
                next(conn.block_storage.volumes(image_id=img.id, all_projects=True),None) is None):
                # not used by any server. good to delete.
                conn.delete_image(img.id)
            else:
                # used by someone. set to community
                conn.image.update_image(img.id, visibility="community")


        # It is deprecated so get rid of the files on disk
        try:
            os.remove("checksums/"+version["image_name"]+"_CHECKSUM")
        except OSError:
            pass

        cleanup_files(version["filename"])





if __name__=="__main__":
    main()
