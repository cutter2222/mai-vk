#!/usr/bin/env bash
# Выполняется на сервере (Ubuntu/Debian) через deploy/install.sh --target server.
# Ставит Docker Engine с compose plugin, создаёт пользователя сервиса и каталоги.
# Повторный запуск ничего не ломает: каждый шаг проверяет текущее состояние.
# Аргументы: $1 — корень сервиса (SERVER_DIR), $2 — имя пользователя сервиса.
# Скрипт приходит по stdin (bash -s), поэтому тело обёрнуто в main, а stdin команд закрыт:
# иначе программа, читающая ввод, съест остаток скрипта.
set -euo pipefail

main() {
  local server_dir="${1:?нужен SERVER_DIR}"
  local service_user="${2:?нужно имя пользователя сервиса}"
  local login_user sudo sub dirs d env_file
  login_user="$(id -un)"

  if [ "$(id -u)" -eq 0 ]; then
    sudo=""
  else
    sudo -n true 2>/dev/null || { echo "ошибка: пользователю $login_user нужен sudo без пароля" >&2; exit 1; }
    sudo="sudo -n"
  fi

  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *) echo "ошибка: поддерживаются Ubuntu и Debian, здесь ${PRETTY_NAME:-неизвестная система}" >&2; exit 1 ;;
  esac

  echo "== Docker"
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    $sudo apt-get update -qq
    $sudo apt-get install -y -qq ca-certificates curl gnupg
    $sudo install -m 0755 -d /etc/apt/keyrings
    if [ ! -f /etc/apt/keyrings/docker.asc ]; then
      curl -fsSL "https://download.docker.com/linux/$ID/gpg" | $sudo tee /etc/apt/keyrings/docker.asc >/dev/null
      $sudo chmod a+r /etc/apt/keyrings/docker.asc
    fi
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" \
      | $sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    $sudo apt-get update -qq
    $sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
  $sudo systemctl enable --now docker >/dev/null
  command -v curl >/dev/null 2>&1 || $sudo apt-get install -y -qq curl

  # На серверах с малой памятью swap страхует от OOM при рендере LibreOffice и сборке образов.
  if [ "$(free -m | awk '/^Swap:/ {print $2}')" -eq 0 ] && [ "$(free -m | awk '/^Mem:/ {print $2}')" -lt 8192 ]; then
    echo "== Swap 2 ГБ"
    $sudo fallocate -l 2G /swapfile
    $sudo chmod 600 /swapfile
    $sudo mkswap -q /swapfile
    $sudo swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | $sudo tee -a /etc/fstab >/dev/null
    echo 'vm.swappiness=10' | $sudo tee /etc/sysctl.d/90-swappiness.conf >/dev/null
    $sudo sysctl -q -p /etc/sysctl.d/90-swappiness.conf
  fi

  echo "== Пользователь сервиса $service_user и каталоги в $server_dir"
  if ! id -u "$service_user" >/dev/null 2>&1; then
    $sudo useradd --system --home-dir "$server_dir" --no-create-home --shell /usr/sbin/nologin "$service_user"
  fi
  $sudo usermod -aG docker "$service_user"
  [ "$login_user" = root ] || $sudo usermod -aG docker,"$service_user" "$login_user"

  # Корень и репозиторий пишет пользователь, под которым идёт выкладка (repo.new, deploys.log);
  # данные принадлежат пользователю сервиса.
  $sudo install -d -m 0755 -o "$login_user" "$server_dir"
  $sudo install -d -m 0755 -o "$login_user" "$server_dir/repo"
  for sub in data artifacts runs backups; do
    $sudo install -d -m 2775 -o "$service_user" -g "$service_user" "$server_dir/$sub"
  done

  env_file="$server_dir/.env"
  if [ ! -f "$env_file" ]; then
    $sudo install -m 0600 -o "$login_user" /dev/null "$env_file"
    {
      echo "# Настройки сервера. Секреты провайдера заполняются здесь вручную, образец: repo/config/.env.example."
      echo "# Строки PD_PUBLIC_URL, PD_SITE_ADDRESS, PD_HTTP_PORT, PD_HTTPS_PORT переписывает deploy.sh."
      echo "PD_ENV=production"
      echo "PD_UID=$(id -u "$service_user")"
      echo "PD_GID=$(id -g "$service_user")"
    } | $sudo tee "$env_file" >/dev/null
  fi

  echo "== Итог"
  echo "система:  ${PRETTY_NAME:-?} ($(uname -m))"
  echo "docker:   $(docker --version | sed 's/Docker version //')"
  echo "compose:  $(docker compose version --short)"
  echo "cpu:      $(nproc) ядер"
  echo "память:   $(free -m | awk '/^Mem:/ {printf "%d МБ всего, %d МБ свободно", $2, $7}')"
  echo "диск:     $(df -h "$server_dir" | awk 'NR==2 {printf "%s свободно из %s (%s)", $4, $2, $6}')"
  dirs=""
  for d in "$server_dir"/*/; do dirs="$dirs$(basename "$d") "; done
  echo "каталоги: $dirs"
}

main "$@" </dev/null
