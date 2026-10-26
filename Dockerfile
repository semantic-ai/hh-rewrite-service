FROM semtech/mu-python-template:feature-fastapi@sha256:cc99d5442b60706b7637de066e2bf78e13c7f59ba9762930ffa87d026fc01305

COPY requirements.txt /usr/src/app/requirements.txt
RUN pip install --no-cache-dir -r /usr/src/app/requirements.txt

COPY . /usr/src/app