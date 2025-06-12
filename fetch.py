#!/usr/bin/python3
"""
fetch.py

Fetches and deprecates images defined in input.json
For more details refer to README.md


"""
import os
import json
import openstack
import requests
from dotenv import load_dotenv


def download_file(url: str, filename: str) -> None:
    """
    Downloads a file from a specified url using chucking
    """

    with requests.get(
            url,
            allow_redirects=True,
            timeout=600,
            stream=True
        ) as r:
        with open(filename,"wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)



def main() -> None:
    """
    Reads defined images from input.json, downloads new images and deprecates old ones
    """

    load_dotenv()

    openstack.enable_logging(debug=True)
    conn = openstack.connect(cloud=os.getenv("CLOUD"))

    input_data = None
    with open("input.json", "r", encoding="utf-8") as f:
        input_data = json.load(f)



    for version in input_data["new"]:
        filename = version["image_url"].split("/")[-1]

        old_checksum = None
        if os.path.exists("checksums/"+filename+"_CHECKSUM"):
            with open("checksums/"+filename+"_CHECKSUM","r", encoding="utf-8") as f:
                old_checksum = f.read()


        new_checksum = next(
            (
                line
                for line in requests.get(version["checksum_url"], timeout=5).text.split('\n')
                    if filename in line
            )
            , old_checksum)


        if new_checksum == old_checksum:
            print(f"{version["image_name"]} already up to date")
            continue

        print(f"Downloading {version["image_name"]}")




        # Download image
        download_file(version["image_url"],filename)



        # convert to raw
        os.system(f"qemu-img convert -f qcow2 -O raw {filename} {filename}.raw")

        new_image = conn.create_image(
            name=version["image_name"],
            filename=filename+".raw",
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
        test_server = conn.create_server(
            name=version["image_name"]+"_TESTSERVER",
            image=new_image,
            flavor="standard.tiny",
            wait=True,
            auto_ip=False,
            network=os.getenv("NETWORK"),
            timeout=360
        )

        found_test_server = conn.get_server_by_id(test_server.id)
        if found_test_server["status"] == "ERROR":
            print(f"Server {test_server.id} failed to start with image {new_image.id}!")
            continue

        conn.delete_server(test_server.id)




        # Remove old ones
        for img in conn.image.images(name=version["image_name"], owner=new_image.owner):
            if img.id == new_image.id:
                continue

            # Check if image is unused
            if (next(conn.compute.servers(image=img.id), None) is None and
                next(conn.block_storage.volumes(image_id=img.id),None) is None):
                # not used by any server. good to delete?
                conn.delete_image(img.id)
            else:
                # used by someone. set to community
                conn.image.update_image(img.id, visibility="community")


        conn.image.update_image(new_image.id, visibility=version["visibility"])


        os.remove(filename) # Cleanup
        os.remove(filename+".raw")

        with open("checksums/"+filename+"_CHECKSUM","w", encoding="utf-8") as f:
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




if __name__=="__main__":
    main()
