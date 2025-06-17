# ImageBuilder

Fetches and deprecates images for openstack. Images to be fetched/deprecated can be defined within the input.json file which the python script reads.

The package `qemu-utils` is required for this script to function properly.

Do remember to provide a clouds.yaml and/or a secure.yaml file. [More info here](https://docs.openstack.org/python-openstackclient/latest/configuration/index.html#configuration-files)

Please remember to define CLOUD and NETWORK in your shell.
Example:
```bash
CLOUD="openstack" NETWORK="project_1234" python3 fetch.py
```

If you wish to disable ping tests, set the environment variable DISABLE_PINGING to anything

input.json should look something like this.
```json
{
    "new": [
        {
            "image_name": "Ubuntu-24.04",
            "distro":"ubuntu",
            "visibility": "public",
            "image_url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
            "checksum_url": "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS"
        }
    ],
    "deprecated": [
        {
            "image_name": "Ubuntu-24.04",
            "filename": "noble-server-cloudimg-amd64.img"
        }       
    ]
}

```