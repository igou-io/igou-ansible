# Decommissioning the rb5009 netbootxyz container

After `deploy_netboot_binaries.yml` has run successfully and you've
smoke-tested PXE boot end-to-end (boot a real or virtual client on
10.10.9.0/24 or 10.10.45.0/24, confirm it pulls the binary from rb5009
and lands on the TrueNAS menu), tear down the rb5009 netbootxyz
container manually.

Each step is idempotent — run them all even on a partial cleanup.

1. Stop and remove the container:

       /container stop [find root-dir=containers/netbootxyz]
       # wait until status=stopped, then:
       /container remove [find root-dir=containers/netbootxyz]

   This wipes containers/netbootxyz/ on flash (the writable layer
   holding /config).

2. Remove the env list (only present if netbootxyz_env_extra was
   non-empty when the container was last deployed):

       /container envs remove [find name=netbootxyz-env]

3. Detach the bridge port and remove the veth:

       /interface bridge port remove [find interface=veth-netbootxyz]
       /interface veth remove [find name=veth-netbootxyz]

4. Remove the image tar:

       /file remove containers/netbootxyz.tar

5. (Optional) verify nothing remains:

       /container print
       /file print where name~"netbootxyz"
