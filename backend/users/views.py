try:
    from rest_framework import generics, status
    from rest_framework.decorators import api_view, permission_classes
    from rest_framework.permissions import AllowAny, IsAuthenticated
    from rest_framework.response import Response
    from rest_framework_simplejwt.tokens import RefreshToken
    from .serializers import UserSerializer, UserRegistrationSerializer
    DRF_AVAILABLE = True
except ImportError:
    DRF_AVAILABLE = False

from django.contrib.auth import authenticate
from .models import User

if DRF_AVAILABLE:
    class RegisterView(generics.CreateAPIView):
        queryset = User.objects.all()
        permission_classes = (AllowAny,)
        serializer_class = UserRegistrationSerializer
else:
    class RegisterView:
        pass

if DRF_AVAILABLE:
    @api_view(['POST'])
    @permission_classes([AllowAny])
    def login_view(request):
        username = request.data.get('username')
        password = request.data.get('password')
        
        if username and password:
            user = authenticate(username=username, password=password)
            if user:
                refresh = RefreshToken.for_user(user)
                return Response({
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                    'user': UserSerializer(user).data
                })
            else:
                return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)
        else:
            return Response({'error': 'Username and password required'}, status=status.HTTP_400_BAD_REQUEST)

    @api_view(['GET'])
    @permission_classes([IsAuthenticated])
    def user_profile(request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)
else:
    def login_view(request):
        pass

    def user_profile(request):
        pass

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from .forms import ForcedPasswordChangeForm

@login_required
def password_change_required(request):
    if not request.user.must_change_password and request.method != 'POST':
        return redirect('dashboard')

    if request.method == 'POST':
        form = ForcedPasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Your password has been updated successfully. Welcome to DBTRAC!")
            return redirect('dashboard')
    else:
        form = ForcedPasswordChangeForm(user=request.user)

    return render(request, 'password_change_required.html', {
        'form': form,
        'title': 'Change Password Required - DBTRAC'
    })
