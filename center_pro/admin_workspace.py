from urllib.parse import urlencode
from django import forms
from django.http import Http404
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count,Q
from django.shortcuts import get_object_or_404,redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from rest_framework.exceptions import APIException
from .models import Center,Membership,AuditEvent
from .access import assign_access,revoke_access

class AssignmentForm(forms.Form):
    name=forms.CharField(max_length=120,label='Name')
    email=forms.EmailField(max_length=254,label='Work email')
    role=forms.ChoiceField(choices=[('owner','Owner'),('staff','Staff')])


def page(site,request):
    base=reverse('admin:center_workspace')
    center_id=request.GET.get('center') or request.POST.get('center')
    if center_id and not str(center_id).isdecimal():raise Http404('Center unavailable.')
    center=get_object_or_404(Center,pk=center_id,directory_active=True) if center_id else None
    form=AssignmentForm(request.POST if request.method=='POST' and request.POST.get('action')=='assign' else None)
    if request.method=='POST':
        if not center:raise Http404('Select a center first.')
        try:
            if request.POST.get('action')=='assign' and form.is_valid():
                assign_access(request.user,center.pk,form.cleaned_data)
                messages.success(request,'Access assigned. The person can request a secure sign-in link from Center login.')
                return redirect(base+'?'+urlencode({'center':center.pk}))
            elif request.POST.get('action')=='revoke':
                revoke_access(request.user,center.pk,request.POST.get('member'))
                messages.success(request,'Center access revoked.')
                return redirect(base+'?'+urlencode({'center':center.pk}))
        except APIException as error:
            detail=error.detail
            if isinstance(detail,(list,tuple)):detail=' '.join(str(item) for item in detail)
            messages.error(request,str(detail))
    section='audit' if request.GET.get('section')=='audit' else 'centers'
    search=request.GET.get('q','').strip()[:120]
    rows=AuditEvent.objects.select_related('center','actor').order_by('-created_at','-id') if section=='audit' else Center.objects.filter(directory_active=True).annotate(owners=Count('membership',filter=Q(membership__role='owner')),staff_count=Count('membership',filter=Q(membership__role='staff'))).order_by('name','id')
    if search:
        rows=rows.filter(Q(center__name__icontains=search)|Q(action__icontains=search)|Q(reference__icontains=search)) if section=='audit' else rows.filter(name__icontains=search)
    page_obj=Paginator(rows,20).get_page(request.GET.get('page'))
    members=Membership.objects.filter(center=center).select_related('user').order_by('role','id') if center else []
    revoke=get_object_or_404(Membership,pk=request.GET['revoke'],center=center) if center and request.GET.get('revoke') else None
    context={**site.each_context(request),'title':'Owner and staff assignments' if center else 'Center workspace & access','center':center,'form':form,'members':members,'revoke_member':revoke,'page_obj':page_obj,'section':section,'search':search,
      'filter_query':urlencode({'q':search,'section':section}),'center_count':Center.objects.filter(directory_active=True).count(),'membership_count':Membership.objects.filter(center__directory_active=True).count(),'audit_count':AuditEvent.objects.count()}
    return TemplateResponse(request,'admin/center_workspace.html',context)
