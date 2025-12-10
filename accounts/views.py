from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

from .forms import UserSignupForm, PassengerProfileForm, UserEmailForm


def signup_view(request):
    if request.method == 'POST':
        form = UserSignupForm(request.POST)
        if form.is_valid():
            user = form.save()

            # Minimal fix: authenticate to bind a backend, then login.
            raw_password = form.cleaned_data['password1']
            auth_user = authenticate(request, username=user.username, password=raw_password)
            if auth_user is not None:
                login(request, auth_user)
            else:
                # Fallback in case of custom auth flow; explicitly provide backend
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')

            return redirect('metro_dashboard')
    else:
        form = UserSignupForm()

    return render(request, 'accounts/signup.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            if user.is_staff:
                return redirect('metro_scanner_scan')
            return redirect('metro_dashboard')
        else:
            error = "Invalid username or password"
            return render(request, 'accounts/login.html', {'error': error})

    return render(request, 'accounts/login.html')


def logout_view(request):
    logout(request)
    return redirect('accounts_login')


@login_required
def profile_edit_view(request):
    profile = request.user.profile

    if request.method == 'POST':
        profile_form = PassengerProfileForm(request.POST, instance=profile)
        email_form = UserEmailForm(request.POST, instance=request.user)

        if profile_form.is_valid() and email_form.is_valid():
            profile_form.save()
            email_form.save()
            return redirect('metro_dashboard')
    else:
        profile_form = PassengerProfileForm(instance=profile)
        email_form = UserEmailForm(instance=request.user)

    return render(request, 'accounts/profile_edit.html', {
        'profile_form': profile_form,
        'email_form': email_form,
    })
