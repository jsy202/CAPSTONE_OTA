#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -lt 3 || $# -gt 4 || ( $# -eq 4 && "$4" != "--force" ) ]]; then
  echo "usage: $0 <hostname-or-ip> <device-id> <output-dir> [--force]" >&2
  exit 2
fi

server_name=$1
device_id=$2
output_dir=$3
force=${4:-}

if [[ ! $device_id =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; then
  echo "invalid device id" >&2
  exit 2
fi

private_names=(ca.key https.key mqtt.key publisher.key "$device_id.key" update-signing.key)
for name in "${private_names[@]}"; do
  if [[ -e "$output_dir/$name" && $force != "--force" ]]; then
    echo "refusing to overwrite existing private key: $output_dir/$name" >&2
    exit 1
  fi
done

mkdir -p -- "$output_dir"
chmod 700 -- "$output_dir"
if [[ $server_name =~ ^[0-9a-fA-F:.]+$ ]]; then
  san="IP:$server_name"
else
  san="DNS:$server_name"
fi

openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
  -subj "/CN=CAPSTONE Development CA" \
  -keyout "$output_dir/ca.key" -out "$output_dir/ca.crt"

issue_certificate() {
  local name=$1
  local common_name=$2
  local purpose=$3
  local san_value=${4:-}
  local ext_file="$output_dir/.$name.ext"
  openssl genrsa -out "$output_dir/$name.key" 3072
  openssl req -new -key "$output_dir/$name.key" -subj "/CN=$common_name" -out "$output_dir/.$name.csr"
  {
    echo "basicConstraints=critical,CA:FALSE"
    echo "keyUsage=critical,digitalSignature,keyEncipherment"
    echo "extendedKeyUsage=$purpose"
    if [[ -n $san_value ]]; then echo "subjectAltName=$san_value"; fi
  } > "$ext_file"
  openssl x509 -req -sha256 -days 825 -in "$output_dir/.$name.csr" \
    -CA "$output_dir/ca.crt" -CAkey "$output_dir/ca.key" -CAcreateserial \
    -extfile "$ext_file" -out "$output_dir/$name.crt"
  rm -f -- "$output_dir/.$name.csr" "$ext_file"
}

issue_certificate https "$server_name" serverAuth "$san"
issue_certificate mqtt "$server_name" serverAuth "$san"
issue_certificate publisher publisher clientAuth
issue_certificate "$device_id" "$device_id" clientAuth
openssl genpkey -algorithm ED25519 -out "$output_dir/update-signing.key"
openssl pkey -in "$output_dir/update-signing.key" -pubout -out "$output_dir/update-signing.pub"
chmod 600 -- "$output_dir"/*
echo "development PKI written to $output_dir"

