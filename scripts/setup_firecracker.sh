#!/bin/bash
# Setup Firecracker for Pantheon skill + code_execute sandboxing.
# Supports both x86_64 and aarch64 (Oracle A1 ARM).
#
# Usage: sudo ./scripts/setup_firecracker.sh
#
# This script:
#   1. Detects architecture
#   2. Downloads Firecracker binary
#   3. Downloads a minimal Linux kernel
#   4. Builds Python and Node rootfs images
#   5. Verifies /dev/kvm access

set -euo pipefail

# Quick prerequisite check (no install) — used by the Settings card.
if [ "${1:-}" = "--check" ]; then
    echo "Checking Firecracker prerequisites..."
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  FC_ARCH="x86_64" ;;
        aarch64|arm64) FC_ARCH="aarch64" ;;
        *) echo "  ERROR: Unsupported arch: $ARCH"; exit 1 ;;
    esac
    [ -f "${FC_DIR:-/opt/firecracker}/bin/firecracker-${FC_ARCH}" ] && echo "  firecracker binary: OK" || echo "  firecracker binary: MISSING"
    [ -f "${FC_DIR:-/opt/firecracker}/kernel/vmlinux-${FC_ARCH}" ] && echo "  kernel: OK" || echo "  kernel: MISSING"
    [ -f "${FC_DIR:-/opt/firecracker}/rootfs/python-base.ext4" ] && echo "  python rootfs: OK" || echo "  python rootfs: MISSING"
    [ -f "${FC_DIR:-/opt/firecracker}/rootfs/node-base.ext4" ] && echo "  node rootfs: OK" || echo "  node rootfs: MISSING"
    [ -e /dev/kvm ] && echo "  /dev/kvm: present" || echo "  /dev/kvm: MISSING"
    exit 0
fi


FC_VERSION="v1.7.0"
FC_DIR="${FC_DIR:-/opt/firecracker}"
ARCH=$(uname -m)

echo "═══════════════════════════════════════════"
echo " Firecracker Setup for Pantheon"
echo " Architecture: $ARCH"
echo " Install dir: $FC_DIR"
echo "═══════════════════════════════════════════"

# Map arch names
case "$ARCH" in
    x86_64)  FC_ARCH="x86_64" ;;
    aarch64) FC_ARCH="aarch64" ;;
    arm64)   FC_ARCH="aarch64" ;;  # macOS reports arm64
    *)
        echo "ERROR: Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

# ── 1. Create directory structure ──
echo ""
echo "── Creating directory structure ──"
mkdir -p "$FC_DIR"/{bin,kernel,rootfs}

# ── 2. Download Firecracker binary ──
echo ""
echo "── Downloading Firecracker $FC_VERSION ($FC_ARCH) ──"
FC_URL="https://github.com/firecracker-microvm/firecracker/releases/download/${FC_VERSION}/firecracker-${FC_VERSION}-${FC_ARCH}.tgz"
FC_BIN="$FC_DIR/bin/firecracker-${FC_ARCH}"

if [ -f "$FC_BIN" ]; then
    echo "  Already exists: $FC_BIN"
else
    TMP=$(mktemp -d)
    curl -sL "$FC_URL" | tar xz -C "$TMP"
    cp "$TMP"/release-${FC_VERSION}-${FC_ARCH}/firecracker-${FC_VERSION}-${FC_ARCH} "$FC_BIN"
    chmod +x "$FC_BIN"
    rm -rf "$TMP"
    echo "  Installed: $FC_BIN"
fi

# Also copy jailer
JAILER_BIN="$FC_DIR/bin/jailer-${FC_ARCH}"
if [ ! -f "$JAILER_BIN" ]; then
    TMP=$(mktemp -d)
    curl -sL "$FC_URL" | tar xz -C "$TMP"
    cp "$TMP"/release-${FC_VERSION}-${FC_ARCH}/jailer-${FC_VERSION}-${FC_ARCH} "$JAILER_BIN" 2>/dev/null || true
    chmod +x "$JAILER_BIN" 2>/dev/null || true
    rm -rf "$TMP"
fi

# ── 3. Download kernel ──
echo ""
echo "── Setting up kernel ──"
KERNEL_PATH="$FC_DIR/kernel/vmlinux-${FC_ARCH}"

if [ -f "$KERNEL_PATH" ]; then
    echo "  Already exists: $KERNEL_PATH"
else
    # Use Firecracker's CI kernels
    KERNEL_URL="https://s3.amazonaws.com/spec.ccfc.min/img/quickstart_guide/${FC_ARCH}/kernels/vmlinux.bin"
    echo "  Downloading kernel from $KERNEL_URL"
    curl -sL "$KERNEL_URL" -o "$KERNEL_PATH" || {
        echo "  WARNING: Could not download kernel. You'll need to provide one at $KERNEL_PATH"
        echo "  See: https://github.com/firecracker-microvm/firecracker/blob/main/docs/rootfs-and-kernel-setup.md"
    }
    echo "  Installed: $KERNEL_PATH"
fi

# ── 4. Build rootfs images ──
echo ""
echo "── Building rootfs images ──"

build_rootfs() {
    local name="$1"
    local packages="$2"
    local rootfs_path="$FC_DIR/rootfs/${name}-base.ext4"

    if [ -f "$rootfs_path" ]; then
        echo "  Already exists: $rootfs_path"
        return
    fi

    echo "  Building $name rootfs..."

    # Create a minimal ext4 filesystem
    local size_mb=512
    local mount_dir=$(mktemp -d)

    dd if=/dev/zero of="$rootfs_path" bs=1M count=$size_mb status=none
    mkfs.ext4 -F -q "$rootfs_path"
    mount -o loop "$rootfs_path" "$mount_dir"

    # Bootstrap a minimal system using debootstrap (Debian/Ubuntu)
    if command -v debootstrap &>/dev/null; then
        local suite="bookworm"  # Debian 12
        debootstrap --variant=minbase --include="$packages" "$suite" "$mount_dir" http://deb.debian.org/debian/ || {
            echo "  WARNING: debootstrap failed. Creating minimal rootfs instead."
            # Fallback: create minimal directory structure
            mkdir -p "$mount_dir"/{bin,sbin,usr/bin,usr/sbin,lib,lib64,etc,tmp,inject,proc,sys,dev}
            # Copy basic binaries
            for bin in sh bash; do
                local binpath=$(which "$bin" 2>/dev/null)
                if [ -n "$binpath" ]; then
                    cp "$binpath" "$mount_dir/bin/"
                fi
            done
        }
    else
        echo "  NOTE: debootstrap not found. Creating minimal rootfs."
        echo "  Install debootstrap for full rootfs: apt install debootstrap"
        mkdir -p "$mount_dir"/{bin,sbin,usr/bin,usr/sbin,lib,lib64,etc,tmp,inject,proc,sys,dev}
    fi

    # Create inject directory for scripts
    mkdir -p "$mount_dir/inject"

    # Create init script
    cat > "$mount_dir/sbin/init" << 'INIT_EOF'
#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sysfs /sys
# Execute the runner script if it exists
if [ -x /inject/_runner.sh ]; then
    /inject/_runner.sh
    echo "EXIT_CODE=$?"
fi
# Power off
reboot -f
INIT_EOF
    chmod +x "$mount_dir/sbin/init"

    umount "$mount_dir"
    rmdir "$mount_dir"
    echo "  Built: $rootfs_path ($size_mb MB)"
}

build_rootfs "python" "python3,python3-pip,ca-certificates"
build_rootfs "node" "nodejs,npm,ca-certificates"

# ── 5. Check /dev/kvm ──
echo ""
echo "── Checking KVM access ──"
if [ -c /dev/kvm ]; then
    echo "  ✓ /dev/kvm is available"
    if [ -w /dev/kvm ]; then
        echo "  ✓ /dev/kvm is writable"
    else
        echo "  ✗ /dev/kvm is not writable by current user"
        echo "    Fix: sudo chmod 666 /dev/kvm"
        echo "    Or: sudo usermod -aG kvm $(whoami)"
    fi
else
    echo "  ✗ /dev/kvm not found"
    echo "    Nested virtualization may need to be enabled."
    echo "    On cloud VMs, check provider settings."
    echo "    Tuatha will fall back to subprocess sandbox."
fi

# ── 6. Summary ──
echo ""
echo "═══════════════════════════════════════════"
echo " Firecracker Setup Summary"
echo "═══════════════════════════════════════════"
echo "  Binary:  $FC_BIN"
echo "  Kernel:  $KERNEL_PATH"
echo "  Rootfs:  $FC_DIR/rootfs/"
ls -1 "$FC_DIR/rootfs/"*.ext4 2>/dev/null | sed 's/^/           /'
echo ""
echo "  To use Firecracker sandbox, set in .env:"
echo "    SANDBOX_BACKEND=firecracker"
echo "    FIRECRACKER_DIR=$FC_DIR"
echo ""
echo "  To use subprocess sandbox (default, no setup needed):"
echo "    SANDBOX_BACKEND=subprocess"
echo "═══════════════════════════════════════════"
