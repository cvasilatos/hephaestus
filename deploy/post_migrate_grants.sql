BEGIN;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM gpu_booking_agent;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM gpu_booking_agent;

GRANT SELECT ON
    booking_siteconfiguration,
    booking_reservation,
    booking_gpuallocation,
    booking_gpu,
    booking_userprofile,
    auth_user
TO gpu_booking_agent;

GRANT UPDATE (
    status,
    term_sent_at,
    access_granted_at,
    access_released_at,
    updated_at
) ON booking_reservation TO gpu_booking_agent;

GRANT SELECT, INSERT ON booking_auditevent TO gpu_booking_agent;
GRANT SELECT, INSERT ON booking_notificationoutbox TO gpu_booking_agent;
GRANT SELECT, INSERT, UPDATE ON booking_gpu TO gpu_booking_agent;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gpu_booking_agent;

COMMIT;
