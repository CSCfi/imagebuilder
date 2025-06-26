# ImageBuilder

Fetches and deprecates images for openstack. Images to be fetched/deprecated can be defined within the input.json file which the python script reads.

Please remember to define IMAGEBUILDER_CLOUD and IMAGEBUILDER_NETWORK in your shell.

If you wish to disable ping tests, set the environment variable IMAGEBUILDER_DISABLE_PINGING to anything
If you wish to skip fetching new checksums, set the environment variable IMAGEBUILDER_SKIP_CHECKSUM to anything
If you wish for a verbose output of all openstack commands being ran set the variable IMAGEBUILDER_DEBUG to anything

This project has been tested to work with Python version 3.12.3 but newer and versions down to 3.9 are very likely to work.

## Getting started:
Install `qemu-utils` (Debian/Ubuntu) or `qemu-img` (AlmaLinux/CentOS/RHEL)
It is required for converting images into RAW files


Create a python virtual environment and activate it like this:
```bash
python3 -m venv env

source env/bin/activate
```
Install required packages from requirements.txt using pip in the virtual environment
```bash
pip install -r requirements.txt
```

Configure a clouds.yaml file according to your needs and place it in one of these 3:
 * the current directory
 * ~/.config/openstack
 * /etc/openstack

[Instructions on how to compile a clouds.yaml file](https://docs.openstack.org/python-openstackclient/latest/configuration/index.html#configuration-files)

Set your environment variables as such:
* IMAGEBUILDER_CLOUD should match your clouds.yaml cloud you would like to use
* IMAGEBUILDER_NETWORK should match the default network the openstack project uses

By default the program will log in your current directory under {cloud}.log if you wish to set your own log file set:
* IMAGEBUILDER_LOG_FILE to the file where you wish to log


Now the project is ready to be run!

## Examples:

The script is run like this:
```bash
IMAGEBUILDER_CLOUD="openstack" IMAGEBUILDER_NETWORK="project_1234" python3 fetch.py
```



input.json should look something like this.
```json
{
    "current": [
        {
            "image_name": "Ubuntu-24.04",
            "visibility": "public",
            "image_url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
            "checksum_url": "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS",
            "properties": {
                "os_distro": "ubuntu"
            }
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

The data in current also accepts custom properties for images. [Here's a list of all possible property values you can set](https://docs.openstack.org/glance/victoria/admin/useful-image-properties.html)
Keep in mind that os_distro is managed by the distro field in your json file.

The data in current can also have a field for "os_type" which can either be set to "linux" or "windows".
By default it is set to "linux" so you only need to specify it if you are running Windows.