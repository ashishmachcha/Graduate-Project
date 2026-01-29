from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import TodoItem
from .forms import TodoItemForm

@login_required
def todo_list(request):
    todo_items = TodoItem.objects.all()
    return render(request, 'todo_list.html', {'todo_items': todo_items})

@login_required
def add_todo_item(request):
    if request.method == 'POST':
        form = TodoItemForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('todo_list')
    else:
        form = TodoItemForm()
    return render(request, 'add_todo_item.html', {'form': form})

@login_required
def delete_todo_item(request, pk):
    TodoItem.objects.get(pk=pk).delete()
    return redirect('todo_list')
