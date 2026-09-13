from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("health/", views.health, name="health"),
    path("cars/<int:pk>/", views.vehicle_detail, name="vehicle"),
    path("cars/<int:pk>/favourite/", views.toggle_favourite, name="toggle_favourite"),
    path("sources/", views.sources, name="sources"),
    path("sources/<int:pk>/action/", views.source_action, name="source_action"),
    path("runs/", views.runs, name="runs"),
    path("duplicates/", views.duplicates, name="duplicates"),
    path("duplicates/<int:pk>/review/", views.review_duplicate, name="review_duplicate"),
    path("listings/<int:pk>/split/", views.split, name="split"),
]
