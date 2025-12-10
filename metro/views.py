from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.contrib.admin.views.decorators import staff_member_required

from django import forms

import random
from datetime import timedelta
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings

from .models import Ticket, WalletTransaction, MetroLine, Station, TicketScan, Connection, PurchaseOTP
from .forms import WalletTopupForm, TicketPurchaseForm, OfflineTicketForm, OTPVerifyForm
from .services import shortest_path_between_stations, calculate_price_from_path

import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.lines as mlines
import networkx as nx
from django.http import HttpResponse
from django.shortcuts import render


def scanner_check(user):
    return user.is_active and user.is_staff


class TicketScanForm(forms.Form):
    ticket_id = forms.CharField(label="Ticket ID")
    station = forms.ModelChoiceField(queryset=Station.objects.all())
    direction = forms.ChoiceField(choices=TicketScan.DIRECTION_CHOICES)


@login_required
def dashboard_view(request):
    profile = request.user.profile
    recent_tickets = profile.tickets.order_by('-created_at')[:5]

    context = {
        'balance': profile.balance,
        'recent_tickets': recent_tickets,
    }
    return render(request, 'metro/dashboard.html', context)


@login_required
def wallet_topup_view(request):
    profile = request.user.profile

    if request.method == 'POST':
        form = WalletTopupForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            profile.balance += amount
            profile.save()

            WalletTransaction.objects.create(
                passenger=profile,
                amount=amount,
                description='Wallet top-up'
            )
            return redirect('metro_dashboard')
    else:
        form = WalletTopupForm()

    return render(request, 'metro/wallet_add.html', {'form': form})


@login_required
def ticket_list_view(request):
    profile = request.user.profile
    tickets = profile.tickets.order_by('-created_at')
    for t in tickets:
        if t.status in ('ACTIVE', 'IN_USE') and t.is_expired():
            t.status = 'EXPIRED'
            t.save()
    return render(request, 'metro/ticket_list.html', {'tickets': tickets})


@login_required
def ticket_detail_view(request, ticket_id):
    profile = request.user.profile
    ticket = get_object_or_404(Ticket, id=ticket_id, passenger=profile)
    if ticket.status in ('ACTIVE', 'IN_USE') and ticket.is_expired():
        ticket.status = 'EXPIRED'
        ticket.save()
    return render(request, 'metro/ticket_detail.html', {'ticket': ticket})


@login_required
def ticket_purchase_view(request):
    profile = request.user.profile
    has_active_line = MetroLine.objects.filter(
        is_enabled=True,
    ).exists()

    if not has_active_line:
        return render(request, 'metro/ticket_buy.html', {
            'form': None,
            'error': "No active metro line is available for ticket purchase at the moment."
        })

    if request.method == 'POST':
        form = TicketPurchaseForm(request.POST)
        if form.is_valid():
            if not request.user.email:
                return render(request, 'metro/ticket_buy.html', { 
                               'form': form, 
                               'error': "You must add an email to receive the OTP. Go to Profile → add your email, then try again."
                             })
            source = form.cleaned_data['source']
            destination = form.cleaned_data['destination']

            path_ids = shortest_path_between_stations(
                source, destination, only_enabled=True
            )
            if not path_ids:
                return render(request, 'metro/ticket_buy.html', {
                    'form': form,
                    'error': "No path found between selected stations."
                })

            price = calculate_price_from_path(path_ids)
            if profile.balance < price:
                return render(request, 'metro/ticket_buy.html', {
                    'form': form,
                    'error': f"Insufficient balance. Ticket costs ₹{price}, your balance is ₹{profile.balance}."
                })

            path_repr = "-".join(Station.objects.get(id=sid).code for sid in path_ids)

            lines_in_order = []
            if len(path_ids) > 1:
                for i in range(len(path_ids) - 1):
                    a = Station.objects.get(id=path_ids[i])
                    b = Station.objects.get(id=path_ids[i + 1])
                    conn = (
                        Connection.objects.filter(from_station=a, to_station=b)
                        .select_related('line')
                        .first()
                        or Connection.objects.filter(from_station=b, to_station=a)
                        .select_related('line')
                        .first()
                    )
                    if conn and conn.line:
                        ln = conn.line.name
                        if ln not in lines_in_order:
                            lines_in_order.append(ln)

            lines_used_str = ", ".join(lines_in_order)

            code = f"{random.randint(0, 999999):06d}"
            PurchaseOTP.objects.create(
                user=request.user,
                code=code,
                purpose='TICKET_PURCHASE',
                payload={
                    'source_id': source.id,
                    'destination_id': destination.id,
                    'path_ids': path_ids,
                    'price': str(price),  
                    'path_repr': path_repr,
                    'lines_used': lines_used_str,
                },
                expires_at=timezone.now() + timedelta(minutes=5),
            )


            send_mail(
                subject="Your Metro Ticket OTP",
                message=f"Your OTP is {code}. It expires in 5 minutes.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[request.user.email],
                fail_silently=False,
            )

            return redirect('metro_ticket_buy_verify_otp')
    else:
        form = TicketPurchaseForm()

    return render(request, 'metro/ticket_buy.html', {'form': form})

@login_required
def ticket_purchase_otp_view(request):
    profile = request.user.profile

    otp = (PurchaseOTP.objects
           .filter(user=request.user, purpose='TICKET_PURCHASE', is_used=False)
           .order_by('-created_at')
           .first())

    if not otp:
        return render(request, 'metro/ticket_buy_otp.html', {
            'form': OTPVerifyForm(),
            'error': "No valid OTP found. Please start purchase again."
        })

    if timezone.now() > otp.expires_at:
        new_code = f"{random.randint(0, 999999):06d}"
        new_otp = PurchaseOTP.objects.create(
            user=request.user,
            code=new_code,
            purpose='TICKET_PURCHASE',
            payload={
                'source_id': otp.payload['source_id'],
                'destination_id': otp.payload['destination_id'],
                'path_ids': otp.payload['path_ids'],
                'price': otp.payload['price'],
                'path_repr': otp.payload['path_repr'],
                'lines_used': otp.payload['lines_used'],
            },
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        send_mail(
            subject="Your Metro Ticket OTP",
            message=f"Your OTP is {new_code}. It expires in 5 minutes.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[request.user.email],
            fail_silently=False,
        )
        return render(request, 'metro/ticket_buy_otp.html', {
            'form': OTPVerifyForm(),
            'info': "Your previous OTP expired. A new OTP has been sent to your email.",
        })

    if request.method == 'GET' and request.GET.get('resend') == '1':
        otp.is_used = True
        otp.save(update_fields=['is_used'])

        new_code = f"{random.randint(0, 999999):06d}"
        new_otp = PurchaseOTP.objects.create(
            user=request.user,
            code=new_code,
            purpose='TICKET_PURCHASE',
            payload=otp.payload,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        send_mail(
            subject="Your Metro Ticket OTP",
            message=f"Your OTP is {new_code}. It expires in 5 minutes.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[request.user.email],
            fail_silently=False,
        )
        return render(request, 'metro/ticket_buy_otp.html', {
            'form': OTPVerifyForm(),
            'info': "A new OTP has been sent to your email.",
        })

    if request.method == 'GET':
        return render(request, 'metro/ticket_buy_otp.html', {
            'form': OTPVerifyForm(),
        })

    form = OTPVerifyForm(request.POST)
    if form.is_valid():
        otp = (PurchaseOTP.objects
               .filter(user=request.user, purpose='TICKET_PURCHASE', is_used=False)
               .order_by('-created_at')
               .first())
        if not otp:
            return render(request, 'metro/ticket_buy_otp.html', {
                'form': OTPVerifyForm(),
                'error': "No valid OTP found. Please start purchase again."
            })

        if timezone.now() > otp.expires_at:
            new_code = f"{random.randint(0, 999999):06d}"
            new_otp = PurchaseOTP.objects.create(
                user=request.user,
                code=new_code,
                purpose='TICKET_PURCHASE',
                payload=otp.payload,
                expires_at=timezone.now() + timedelta(minutes=5),
            )
            send_mail(
                subject="Your Metro Ticket OTP",
                message=f"Your OTP is {new_code}. It expires in 5 minutes.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[request.user.email],
                fail_silently=False,
            )
            return render(request, 'metro/ticket_buy_otp.html', {
                'form': OTPVerifyForm(),
                'info': "Your OTP expired. We’ve sent a new one to your email.",
            })

        if form.cleaned_data['code'] != otp.code:
            return render(request, 'metro/ticket_buy_otp.html', {
                'form': form,
                'error': "Invalid OTP."
            })

        otp.is_used = True
        otp.save(update_fields=['is_used'])

        from decimal import Decimal
        price = Decimal(otp.payload['price'])

        if profile.balance < price:
            return render(request, 'metro/ticket_buy_otp.html', {
                'form': form,
                'error': "Insufficient balance at verification time."
            })

        source = Station.objects.get(id=otp.payload['source_id'])
        destination = Station.objects.get(id=otp.payload['destination_id'])
        path_repr = otp.payload['path_repr']
        lines_used_str = otp.payload['lines_used']

        profile.balance -= price
        profile.save()
        WalletTransaction.objects.create(
            passenger=profile,
            amount=-price,
            description=f'Ticket purchase {source.code}->{destination.code}'
        )

        expiry = timezone.now() + timedelta(days=1)
        ticket = Ticket.objects.create(
            passenger=profile,
            source=source,
            destination=destination,
            price=price,
            path_repr=path_repr,
            lines_used=lines_used_str,
            expires_at=expiry,
        )

        send_mail(
            subject="Metro Ticket Purchased",
            message=f"Ticket {ticket.id} from {source.name} to {destination.name} purchased for ₹{price}.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[request.user.email],
            fail_silently=True,
        )

        return redirect('metro_ticket_detail', ticket_id=ticket.id)

    return render(request, 'metro/ticket_buy_otp.html', {'form': OTPVerifyForm()})


@user_passes_test(scanner_check)
def scanner_scan_view(request):
    message = None

    if request.method == 'POST':
        form = TicketScanForm(request.POST)
        if form.is_valid():
            ticket_id = form.cleaned_data['ticket_id']
            station = form.cleaned_data['station']
            direction = form.cleaned_data['direction']

            ticket = get_object_or_404(Ticket, id=ticket_id)

            if ticket.status in ('USED', 'EXPIRED'):
                message = f"Cannot scan. Ticket status is {ticket.status}."
                return render(request, 'metro/scanner_scan.html', {'form': form, 'message': message})

            if direction == 'ENTRY':
                if ticket.status != 'ACTIVE':
                    message = f"ENTRY denied. Ticket status is {ticket.status}, expected ACTIVE."
                elif station.id != ticket.source_id:
                    message = (
                        f"ENTRY denied at {station.name}. "
                        f"Ticket source is {ticket.source.name}."
                    )
                else:
                    ticket.status = 'IN_USE'
                    ticket.save()
                    TicketScan.objects.create(
                        ticket=ticket,
                        station=station,
                        direction='ENTRY',
                        scanned_by=request.user,
                    )
                    message = "Entry scan successful. Ticket is now IN_USE."
            else: 
                if ticket.status != 'IN_USE':
                    message = f"EXIT denied. Ticket status is {ticket.status}, expected IN_USE."
                elif station.id != ticket.destination_id:
                    message = (
                        f"EXIT denied at {station.name}. "
                        f"Ticket destination is {ticket.destination.name}."
                    )
                else:
                    ticket.status = 'USED'
                    ticket.save()
                    TicketScan.objects.create(
                        ticket=ticket,
                        station=station,
                        direction='EXIT',
                        scanned_by=request.user,
                    )
                    message = "Exit scan successful. Ticket is now USED."
    else:
        form = TicketScanForm()

    return render(request, 'metro/scanner_scan.html', {'form': form, 'message': message})


@staff_member_required
def scanner_offline_ticket_view(request):
    message = None
    ticket_obj = None

    if request.method == 'POST':
        form = OfflineTicketForm(request.POST)
        if form.is_valid():
            source = form.cleaned_data['source']
            destination = form.cleaned_data['destination']

            path_ids = shortest_path_between_stations(source, destination, only_enabled=True)
            if not path_ids:
                message = "No path found between selected stations."
            else:
                price = calculate_price_from_path(path_ids)

                path_repr = "-".join(Station.objects.get(id=sid).code for sid in path_ids)

                lines_in_order = []
                if len(path_ids) > 1:
                    for i in range(len(path_ids) - 1):
                        a = Station.objects.get(id=path_ids[i])
                        b = Station.objects.get(id=path_ids[i+1])

                        conn = Connection.objects.filter(from_station=a, to_station=b).select_related('line').first()
                        if not conn:
                            conn = Connection.objects.filter(from_station=b, to_station=a).select_related('line').first()

                        if conn and conn.line:
                            ln = conn.line.name
                            if ln not in lines_in_order:
                                lines_in_order.append(ln)

                lines_used_str = ", ".join(lines_in_order)

                ticket_obj = Ticket.objects.create(
                    passenger=None,
                    source=source,
                    destination=destination,
                    price=price,
                    status='USED', 
                    path_repr=path_repr,
                    lines_used=lines_used_str,
                    expires_at=timezone.now() + timedelta(days=1), 
                )
                TicketScan.objects.create(
                    ticket=ticket_obj,
                    station=source,
                    direction='ENTRY',
                    scanned_by=request.user
                )
                TicketScan.objects.create(
                    ticket=ticket_obj,
                    station=destination,
                    direction='EXIT',
                    scanned_by=request.user
                )
                message = f"Offline ticket created and marked as USED. Ticket ID: {ticket_obj.id}"
    else:
        form = OfflineTicketForm()

    return render(request, 'metro/scanner_offline_ticket.html', {
        'form': form,
        'message': message,
        'ticket': ticket_obj,
    })

@staff_member_required
def footfall_report_view(request):

    scans = (
        TicketScan.objects
        .annotate(day=TruncDate('scanned_at'))
        .values('day', 'station__name')
        .annotate(count=Count('id'))
        .order_by('-day', 'station__name')
    )

    context = {
        'rows': scans,
    }
    return render(request, 'metro/admin_footfall.html', context)


LINE_COLOR_MAP = {
    'R': 'red',
    'B': 'blue',
    'O': 'orange',
    'Y': 'gold',
    'G': 'green'
}

def build_graph_from_db():
    G = nx.Graph()
    from .models import Station, Connection

    for st in Station.objects.all():
        G.add_node(st.code, label=st.name)

    # Add edges with line id stored
    for conn in Connection.objects.select_related('line', 'from_station', 'to_station').all():
        a = conn.from_station.code
        b = conn.to_station.code
        G.add_edge(a, b, line=conn.line.code if conn.line else None)

    return G

def metro_map_image(request):
    from .models import Ticket

    G = build_graph_from_db()

    pos = nx.spring_layout(G, seed=42)

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111)
    ax.set_title("Metro Map", fontsize=14, fontweight='bold')

    edges_by_line = {}
    for u, v, d in G.edges(data=True):
        line_id = d.get('line') or 'UNKNOWN'
        edges_by_line.setdefault(line_id, []).append((u, v))

    for line_id, edges in edges_by_line.items():
        color = LINE_COLOR_MAP.get(line_id, 'gray')
        nx.draw_networkx_edges(G, pos, edgelist=edges, width=2.5, edge_color=color, alpha=0.9, ax=ax)

    nx.draw_networkx_nodes(G, pos, node_size=400, node_color='lightgray', ax=ax)
    labels = {n: G.nodes[n].get('label', n) for n in G.nodes()}
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=8, ax=ax)

    legend_handles = []
    for line_id, color in LINE_COLOR_MAP.items():
        from .models import MetroLine
        line_obj = MetroLine.objects.filter(code=line_id).first()
        line_name = line_obj.name if line_obj else line_id
        legend_handles.append(mlines.Line2D([], [], color=color, marker='_', markersize=15, label=f"{line_name}"))
    ax.legend(handles=legend_handles, title="Lines", loc='upper left')

    ticket_id = request.GET.get('highlight')
    if ticket_id:
        try:
            ticket = Ticket.objects.get(id=ticket_id)
            path_codes = ticket.path_repr.split('-') if ticket.path_repr else []
            path_edges = list(zip(path_codes, path_codes[1:])) if len(path_codes) > 1 else []
            if path_edges:
                nx.draw_networkx_edges(G, pos, edgelist=path_edges, width=4.0, edge_color='black', style='dashed', ax=ax)
                nx.draw_networkx_nodes(G, pos, nodelist=path_codes, node_size=500, node_color='yellow', ax=ax)
        except Ticket.DoesNotExist:
            pass

    ax.axis('off')
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150)
    plt.close(fig)
    buf.seek(0)
    return HttpResponse(buf.getvalue(), content_type='image/png')

@login_required
def metro_map_view(request):
    return render(request, 'metro/metro_map.html', {})


