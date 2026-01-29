from rest_framework.response import Response
from rest_framework.views import APIView
from .models import TodoItem
from .serializers import TodoItemSerializer

class TodoItemView(APIView):
    def get(self, request):
        todo_items = TodoItem.objects.all()
        serializer = TodoItemSerializer(todo_items, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = TodoItemSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

class TodoItemDetailView(APIView):
    def get(self, request, pk):
        try:
            todo_item = TodoItem.objects.get(pk=pk)
        except TodoItem.DoesNotExist:
            return Response({'error': 'Todo item not found'}, status=404)
        serializer = TodoItemSerializer(todo_item)
        return Response(serializer.data)

    def put(self, request, pk):
        try:
            todo_item = TodoItem.objects.get(pk=pk)
        except TodoItem.DoesNotExist:
            return Response({'error': 'Todo item not found'}, status=404)
        serializer = TodoItemSerializer(todo_item, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def delete(self, request, pk):
        try:
            todo_item = TodoItem.objects.get(pk=pk)
        except TodoItem.DoesNotExist:
            return Response({'error': 'Todo item not found'}, status=404)
        todo_item.delete()
        return Response({'message': 'Todo item deleted'}, status=204)
