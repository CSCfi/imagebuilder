FROM python:3.12

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update; apt-get install qemu-utils -y

COPY fetch.py .

CMD ["python","fetch.py"]