#!/usr/bin/env bash

ANSIBLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$ANSIBLE_DIR/.." && pwd)"
INVENTORY="$ANSIBLE_DIR/inventory/hosts.yml"
VENV_PATH="$PROJECT_DIR/.venv"
ENV_FILE="$PROJECT_DIR/.env"

if [ -d "$VENV_PATH" ]; then
  source "$VENV_PATH/bin/activate"
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

cd "$ANSIBLE_DIR" || return 1

bootstrap() {
  ansible-playbook -i "$INVENTORY" "$ANSIBLE_DIR/playbooks/bootstrap.yml"
}

postgres() {
  ansible-playbook -i "$INVENTORY" "$ANSIBLE_DIR/playbooks/deploy_postgres.yml"
}

backend() {
  ansible-playbook -i "$INVENTORY" "$ANSIBLE_DIR/playbooks/deploy_backend.yml"
}

bot() {
  ansible-playbook -i "$INVENTORY" "$ANSIBLE_DIR/playbooks/deploy_bot.yml"
}

wipe() {
  ansible-playbook -i "$INVENTORY" "$ANSIBLE_DIR/playbooks/wipe.yml"
}

all_deploy() {
  bootstrap &&
  postgres &&
  backend &&
  bot
}

ping() {
  ansible -i "$INVENTORY" vibro -m ping
}

status() {
  ssh vibro-monitor 'cd /opt/vibro && docker compose ps'
}

blogs() {
  ssh vibro-monitor 'cd /opt/vibro && docker compose logs backend --tail=100'
}

plogs() {
  ssh vibro-monitor 'cd /opt/vibro && docker compose logs postgres --tail=100'
}

tlogs() {
  ssh vibro-monitor 'cd /opt/vibro && docker compose logs bot --tail=100'
}

echo "Environment loaded."
echo "Directory: $ANSIBLE_DIR"
echo "Commands: ping, bootstrap, postgres, backend, bot, wipe, all_deploy, status, blogs, plogs, tlogs"
