from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("health/", views.health, name="health"),
    path("", views.dashboard, name="dashboard"),
    path("policy/", views.policy, name="policy"),
    path("profile/email/", views.profile_email, name="profile-email"),
    path("profile/email/verify/<str:token>/", views.verify_email, name="verify-email"),
    path("reservations/", views.my_reservations, name="my-reservations"),
    path("reservations/new/", views.reservation_create, name="reservation-create"),
    path(
        "reservations/<int:reservation_id>/edit/", views.reservation_edit, name="reservation-edit"
    ),
    path(
        "reservations/<int:reservation_id>/cancel/",
        views.reservation_cancel,
        name="reservation-cancel",
    ),
    path("control/", views.admin_overview, name="admin-overview"),
    path("control/settings/", views.admin_settings, name="admin-settings"),
    path("control/groups/new/", views.admin_group_edit, name="admin-group-create"),
    path(
        "control/groups/<int:group_id>/edit/",
        views.admin_group_edit,
        name="admin-group-edit",
    ),
    path(
        "control/gpus/<int:gpu_id>/settings/",
        views.admin_gpu_settings,
        name="admin-gpu-settings",
    ),
    path("control/maintenance/new/", views.maintenance_create, name="maintenance-create"),
    path(
        "control/reservations/<int:reservation_id>/cancel/",
        views.admin_cancel_reservation,
        name="admin-reservation-cancel",
    ),
    path(
        "control/reservations/<int:reservation_id>/retry/",
        views.admin_retry_reservation,
        name="admin-reservation-retry",
    ),
    path(
        "control/users/<int:profile_id>/toggle/", views.admin_toggle_user, name="admin-user-toggle"
    ),
    path("control/gpus/<int:gpu_id>/toggle/", views.admin_toggle_gpu, name="admin-gpu-toggle"),
]
