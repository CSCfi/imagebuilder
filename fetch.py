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


def download_file(url: str, filename: str, new_checksum: str) -> bool:
    """
    Downloads a file from a specified url using chucking
    Also converts it to a .raw file
    """
    if os.path.exists("tmp/"+filename):
        hash256 = hashlib.sha256()
        with open("tmp/"+filename, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hash256.update(chunk)

        if hash256.hexdigest().lower() == new_checksum.split(" ")[0].lower():
            print("File already exists on disk.")
            return True


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
        os.system(f"qemu-img convert -f qcow2 -O raw tmp/{filename} tmp/{filename}.raw")

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
                    if filename in line
            ), old_checksum)

    except requests.exceptions.HTTPError:
        error = True
        print(f"HTTP error while downloading {image_name}")

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




def test_image(conn: openstack.connection.Connection, image: any, network: str) -> bool:
    """
    Test the newly created image by creating a test server
    """

    test_server = conn.create_server(
        name=image.name+"_TESTSERVER",
        image=image,
        flavor="standard.tiny",
        wait=True,
        auto_ip=False,
        network=network,
        timeout=360
    )

    found_test_server = conn.get_server_by_id(test_server.id)


    if found_test_server["status"] == "ERROR":
        conn.delete_server(test_server.id)
        print(f"Server {test_server.id} failed to start with image {image.id}!")
        return False



    conn.delete_server(test_server.id)


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



    # Test image
    if not test_image(conn, new_image, network):
        return None




    return new_image




def main() -> None:
    """
    Reads defined images from input.json, downloads new images and deprecates old ones
    """

    cloud = os.getenv("CLOUD") # get it once to avoid potential race conditions
    network = os.getenv("NETWORK")


    openstack.enable_logging(debug=True)
    conn = openstack.connect(cloud=cloud)

    input_data = None
    with open("input.json", "r", encoding="utf-8") as f:
        input_data = json.load(f)



    for version in input_data["new"]:
        filename = version["image_url"].split("/")[-1]

        new_checksum = validate_checksum(version["checksum_url"], filename,
                                         version["image_name"], cloud)

        if new_checksum is None:
            continue



        print(f"Downloading {version["image_name"]}")

        new_image = create_image(conn, version, filename, new_checksum, network)

        if new_image is None:
            print("An error occured while creating the image")
            continue



        # Remove old ones
        for img in conn.image.images(name=version["image_name"], owner=new_image.owner):
            if img.id == new_image.id:
                continue

            # Check if image is unused
            if (next(conn.compute.servers(image=img.id), None) is None and
                next(conn.block_storage.volumes(image_id=img.id), None) is None):
                # not used by any server. good to delete?
                conn.delete_image(img.id)
            else:
                # used by someone. set to community
                conn.image.update_image(img.id, visibility="community")


        # Everything is ok! Set visibility to what it should be
        conn.image.update_image(new_image.id, visibility=version["visibility"])



        with open("checksums/"+cloud+"_"+version["image_name"]+"_CHECKSUM","w",
                  encoding="utf-8") as f:
            f.write(new_checksum)


    for version in input_data["deprecated"]:
        # Loop over existing images
        for img in conn.image.images(name=version["image_name"], owner=conn.current_project_id):

            # Check if image is unused
            if (next(conn.compute.servers(image=img.id), None) is None and
                next(conn.block_storage.volumes(image_id=img.id),None) is None):
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
