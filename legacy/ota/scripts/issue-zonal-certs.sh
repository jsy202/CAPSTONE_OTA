#!/usr/bin/env bash
# Issue the two zonal certificates from an existing generate-dev-pki.sh CA:
#   central-pi-01.{crt,key}  MQTT client (CN must equal the device_id for ACL)
#   central-https.{crt,key}  private cache server, SAN IP:10.10.0.1
set -euo pipefail
umask 077

if [[ $# -ne 1 ]]; then echo "usage: $0 <pki-dir>" >&2; exit 2; fi
pki=$1
for required in ca.crt ca.key; do
  [[ -f $pki/$required ]] || { echo "missing $pki/$required; run generate-dev-pki.sh first" >&2; exit 1; }
done
for name in central-pi-01 central-https; do
  if [[ -e $pki/$name.key ]]; then echo "refusing to overwrite $pki/$name.key" >&2; exit 1; fi
done

issue() {
  local name=$1 cn=$2 purpose=$3 san=${4:-}
  openssl genrsa -out "$pki/$name.key" 3072
  openssl req -new -key "$pki/$name.key" -subj "/CN=$cn" -out "$pki/.$name.csr"
  {
    echo "basicConstraints=critical,CA:FALSE"
    echo "keyUsage=critical,digitalSignature,keyEncipherment"
    echo "extendedKeyUsage=$purpose"
    if [[ -n $san ]]; then echo "subjectAltName=$san"; fi
  } > "$pki/.$name.ext"
  openssl x509 -req -sha256 -days 825 -in "$pki/.$name.csr" -CA "$pki/ca.crt" -CAkey "$pki/ca.key" \
    -CAcreateserial -extfile "$pki/.$name.ext" -out "$pki/$name.crt"
  rm -f -- "$pki/.$name.csr" "$pki/.$name.ext"
}

issue central-pi-01 central-pi-01 clientAuth
issue central-https 10.10.0.1 serverAuth IP:10.10.0.1
chmod 600 -- "$pki"/central-pi-01.* "$pki"/central-https.*
echo "zonal certificates written to $pki"
