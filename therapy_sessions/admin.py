from django.contrib import admin
from .models import Speaker, Session, Pairing, Report

admin.site.register(Speaker)
admin.site.register(Session)
admin.site.register(Pairing)
admin.site.register(Report)
