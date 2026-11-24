#!/bin/bash
# armbian/build chroot customization hook. userpatches/ on the
# builder is shared across boards and persists between builds
# (collection CLAUDE.md), so this file must guard on BOARD and
# stay a no-op for every other board.
#
# rock-5a: install the Radxa 25W PoE HAT fan overlay. The HAT
# fan is a software PWM on GPIO1_B4, inverted polarity (per
# Radxa's vendor rock-5a-radxa-25w-poe.dts); the mainline
# rk3588s-rock-5a.dtb pwm-fan node instead drives PWM3_IR_M1
# into the board's empty onboard 2-pin fan header, so the HAT
# fan never spins and the SoC idles >60C. local_kernel boot
# loads the packaged dtb raw via u-boot localcmd (no boot.scr),
# so armbianEnv.txt user_overlays cannot deliver this — a
# systemd unit applies the overlay through configfs at every
# boot instead, which also survives apt kernel/dtb updates.
set -euo pipefail

BOARD="${3:-}"
[[ "${BOARD}" == "rock-5a" ]] || exit 0

apt-get -y --no-install-recommends install device-tree-compiler

install -d -m 0755 /boot/overlay-user
cat > /boot/overlay-user/radxa-25w-poe-fan.dts <<'EOF_DTS'
/dts-v1/;
/plugin/;

/*
 * Radxa 25W PoE HAT fan on ROCK 5A: retarget the existing
 * mainline fan node's pwms to a pwm-gpio provider on GPIO1_B4
 * so the stock thermal trips/cooling maps keep working. 10 ms
 * period keeps the softPWM hrtimer load negligible; duty 0%
 * and 100% (idle / full fan) toggle nothing at all.
 */

/ {
    fragment@0 {
        target-path = "/";
        __overlay__ {
            radxa_poe_pwm: radxa-poe-pwm {
                compatible = "pwm-gpio";
                #pwm-cells = <3>;
                gpios = <&gpio1 12 0>; /* GPIO1_B4, active high */
            };
        };
    };

    fragment@1 {
        target = <&fan>;
        __overlay__ {
            pwms = <&radxa_poe_pwm 0 10000000 1>; /* 10 ms, inverted */
        };
    };
};
EOF_DTS

cat > /usr/local/sbin/radxa-poe-fan-overlay <<'EOF_SCRIPT'
#!/bin/bash
# Apply the Radxa 25W PoE HAT fan device-tree overlay at runtime.
set -euo pipefail

dts=/boot/overlay-user/radxa-25w-poe-fan.dts
dtbo=/boot/overlay-user/radxa-25w-poe-fan.dtbo
sysdir=/sys/kernel/config/device-tree/overlays/radxa-25w-poe-fan

if [[ ! -e "$dtbo" || "$dts" -nt "$dtbo" ]]; then
    dtc -@ -I dts -O dtb -o "$dtbo" "$dts"
fi

modprobe pwm-gpio
if [[ ! -d "$sysdir" ]]; then
    mkdir "$sysdir"
    cat "$dtbo" > "$sysdir/dtbo"
fi
[[ "$(cat "$sysdir/status")" == "applied" ]]

# pwm-fan may already have probed against the empty onboard fan
# header's PWM before the overlay retargeted the node; rebind so
# the driver re-reads pwms and picks up the pwm-gpio provider.
modprobe pwm-fan
if [[ -e /sys/bus/platform/drivers/pwm-fan/pwm-fan ]]; then
    echo pwm-fan > /sys/bus/platform/drivers/pwm-fan/unbind
fi
for _ in 1 2 3; do
    if echo pwm-fan > /sys/bus/platform/drivers/pwm-fan/bind 2>/dev/null; then
        exit 0
    fi
    sleep 1
done
echo "radxa-poe-fan-overlay: pwm-fan bind failed" >&2
exit 1
EOF_SCRIPT
chmod 0755 /usr/local/sbin/radxa-poe-fan-overlay

cat > /etc/systemd/system/radxa-poe-fan.service <<'EOF_UNIT'
[Unit]
Description=Radxa 25W PoE HAT fan device-tree overlay
Requires=sys-kernel-config.mount
After=sys-kernel-config.mount systemd-modules-load.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/radxa-poe-fan-overlay

[Install]
WantedBy=multi-user.target
EOF_UNIT

systemctl enable radxa-poe-fan.service
