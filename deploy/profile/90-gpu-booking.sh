# Apply the active reservation to newly opened interactive shells. This is a usability aid;
# numeric /dev/nvidiaN ACLs remain the security boundary.
if [ -n "${UID:-}" ] && [ -r "/run/gpu-booking/users/$UID" ]; then
    CUDA_VISIBLE_DEVICES="$(tr -d '\n' < "/run/gpu-booking/users/$UID")"
else
    CUDA_VISIBLE_DEVICES=""
fi
export CUDA_VISIBLE_DEVICES
