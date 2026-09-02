#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y ca-certificates curl

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
CODENAME="${VERSION_CODENAME:-noble}"

write_docker_list() {
  local codename="$1"
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
    https://download.docker.com/linux/ubuntu \
    ${codename} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
}

write_docker_list "${CODENAME}"

if ! sudo apt-get update; then
  echo "Docker apt repo update failed for ${CODENAME}; falling back to noble"
  write_docker_list "noble"
  sudo apt-get update
fi

if ! sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin; then
  if [[ "${CODENAME}" != "noble" ]]; then
    echo "Docker package install failed for ${CODENAME}; falling back to noble"
    write_docker_list "noble"
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  else
    exit 1
  fi
fi

sudo systemctl enable --now docker || sudo service docker start
sudo docker version
sudo docker compose version
