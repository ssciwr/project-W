#!/usr/bin/env bash

error_help_exit () {
    >&2 echo "Error: $1"
    echo "Usage: ./build-container.sh <container to build>"
    echo "Possible values for <container to build>: 'backend', 'cron', 'runner', 'runner_dummy'"
    exit 1
}

if (( $# != 1 )); then
    error_help_exit "Illegal number of parameters"
fi

case $1 in
    backend)
        container_name="project-w" ;;
    cron)
        container_name="project-w_cron" ;;
    runner)
        container_name="project-w_runner" ;;
    runner_dummy)
        container_name="project-w_runner_dummy" ;;
    *)
        error_help_exit "$1 is not a valid argument"
esac

dockerfile="$1.Dockerfile"

if [ ! -f "$(pwd)/$dockerfile" ]; then
    error_help_exit "Couldn't find $dockerfile in current directory. Make sure you are running this script in the directory where $dockerfile is located!"
fi

mount_dir="/tmp/work"
cachedir="/tmp/project-w-docker-build-cache/$1/"

mkdir -p "$cachedir"

docker run \
    --rm \
    --privileged \
    -v "$(pwd):$mount_dir" \
    -v "$cachedir:$cachedir" \
    --entrypoint buildctl-daemonless.sh \
    moby/buildkit:master \
        build \
        --frontend dockerfile.v0 \
        --local "context=$mount_dir" \
        --local "dockerfile=$mount_dir" \
        --import-cache "type=local,src=$cachedir" \
        --export-cache "type=local,dest=$cachedir" \
        --opt "filename=$dockerfile" \
        --output "type=oci,name=ghcr.io/ssciwr/$container_name,dest=-" \
| docker load
