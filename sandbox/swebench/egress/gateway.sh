#!/bin/sh
# Owns the network namespace that exactly one grader joins (--network container:<gateway>).
# Every uid except the gateway's own dnsmasq and proxy daemons is restricted:
#   - DNS only via local dnsmasq, which answers only the task's allowlisted names;
#   - TCP 80/443 to IPs dnsmasq resolved for those names is redirected to a local proxy
#     that forwards only when the TLS SNI or HTTP Host is itself allowlisted;
#   - all other off-host packets route into a dummy device and are silently discarded,
#     so connects time out as upstream's unroutable-address tests expect.
set -eu
ALLOW=${ALLOW-}
DNSMASQ_UID=$(id -u dnsmasq)
PROXY_UID=$(id -u egressproxy)
MARK=0x1
ipset create allow hash:ip -exist
ip link add sink0 type dummy
ip link set sink0 up
ip route add default dev sink0 table 100
ip rule add fwmark "$MARK" lookup 100 priority 100
for table in filter mangle nat; do
  iptables -t "$table" -N RESTRICT
  iptables -t "$table" -A OUTPUT -m owner --uid-owner "$DNSMASQ_UID" -j RETURN
  iptables -t "$table" -A OUTPUT -m owner --uid-owner "$PROXY_UID" -j RETURN
  iptables -t "$table" -A OUTPUT -j RESTRICT
done
iptables -t mangle -A RESTRICT -m addrtype ! --dst-type LOCAL -j MARK --set-mark "$MARK"
iptables -t nat -A RESTRICT -p tcp --dport 80 -m set --match-set allow dst \
  -j REDIRECT --to-ports 8080
iptables -t nat -A RESTRICT -p tcp --dport 443 -m set --match-set allow dst \
  -j REDIRECT --to-ports 8443
# Docker's embedded resolver would answer any name.
iptables -A RESTRICT -d 127.0.0.11 -j REJECT
printf 'nameserver 127.0.0.1\n' > /etc/resolv.conf
conf=/tmp/dnsmasq.conf
{
  echo no-resolv; echo no-hosts; echo log-queries; echo log-facility=-; echo pid-file=
  echo listen-address=127.0.0.1; echo bind-interfaces
  echo "address=/#/"
  for d in $(echo "$ALLOW" | tr ',' ' '); do echo "server=/$d/127.0.0.11"; echo "ipset=/$d/allow"; done
} > "$conf"
python3 /opt/egress/egress_proxy.py &
echo "GATEWAY_READY allow=$ALLOW"
exec dnsmasq -k -C "$conf"
