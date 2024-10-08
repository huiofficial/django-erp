# -*- encoding: utf-8 -*-
"""
Copyright (c) 2019 - present AppSeed.us
"""

import logging
import os
from datetime import datetime, time, timedelta
from io import BytesIO

import pytz
import qrcode
from django import template
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models import Sum
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.template import loader
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import ListView
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, PageBreak

from .forms import CustomUserChangeForm, ProcessForm
from .models import CustomUser
from .models import Order, OrderProduct
from .models import Process, Raw, Product
from .models import Task, Device
from .models import Weight
from .preprocess import preprocess_order, preprocess_product, preprocess_process, preprocess_device, preprocess_raw
from .views_login import login_view, register_user

__all__ = [login_view, register_user]

# views.py

logger = logging.getLogger(__name__)
font_path = 'apps/static/assets/fonts/SourceHanSansCN-Medium.ttf'
pdfmetrics.registerFont(TTFont('SourceHanSansCN', font_path))


@login_required(login_url="/login/")
def user_list_list(request):
    users = CustomUser.objects.all()
    return render(request, 'home/user_list.html', {'users': users})


@login_required(login_url="/login/")
def user_list_get(request, user_id):
    user = get_object_or_404(CustomUser, id=user_id)
    data = {
        'username': user.username,
        'phone_number': user.phone_number,
        'role': user.role,
    }
    return JsonResponse(data)


@csrf_exempt
@login_required(login_url="/login/")
def user_list_update(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(CustomUser, id=user_id)
        form = CustomUserChangeForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            return JsonResponse({'status': 'success'})
        else:
            errors = form.errors.as_json()
            return JsonResponse({'status': 'error', 'errors': errors})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'})


@csrf_exempt
@login_required(login_url="/login/")
def user_list_delete(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(CustomUser, id=user_id)
        user.delete()
        return JsonResponse({'status': 'success'})


@csrf_exempt
@login_required(login_url="/login/")
def user_list_create(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')
        phone_number = request.POST.get('phone_number')
        role = request.POST.get('role')

        if password1 != password2:
            return JsonResponse({'error': 'Passwords do not match.'}, status=400)

        if CustomUser.objects.filter(username=username).exists():
            return JsonResponse({'error': 'Username already exists.'}, status=400)

        try:
            user = CustomUser.objects.create_user(username=username, password=password1, phone_number=phone_number,
                                                  role=role)
            # You can add extra logic to save phone_number and role in a custom user profile model
            user.save()
            # Optionally log in the user after creation
            # user = authenticate(username=username, password=password1)
            # if user is not None:
            #     login(request, user)
            return JsonResponse({'success': 'User created successfully.'})
        except ValidationError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            return JsonResponse({'error': 'An error occurred while creating the user.'}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method.'}, status=405)


@login_required(login_url="/login/")
def index(request):
    from collections import Counter
    # 获取各个模型的数量
    orders_count = Order.objects.count()
    products_count = Product.objects.count()
    raw_count = Raw.objects.count()
    exchange_count = Device.objects.count()
    process_count = Process.objects.count()

    # 获取最新的 weight 数据
    # weight = Weight.objects.latest('id').weight

    # 统计订单的开始日期和交付日期
    orders = Order.objects.all()

    start_date_counts = Counter()
    end_date_counts = Counter()

    for order in orders:
        start_date = parse_date(order.order_start_date)
        end_date = parse_date(order.order_end_date)

        if start_date:
            start_date_counts[start_date] += 1
        if end_date:
            end_date_counts[end_date] += 1

    # 准备数据用于传递给模板
    start_dates = list(start_date_counts.keys())
    start_date_counts_list = list(start_date_counts.values())
    end_dates = list(end_date_counts.keys())
    end_date_counts_list = list(end_date_counts.values())

    # 设定日期和工作时间范围
    selected_date = timezone.make_aware(datetime(2024, 1, 10))
    morning_start_time = timezone.make_aware(datetime.combine(selected_date, time(9, 0, 0)))
    morning_end_time = timezone.make_aware(datetime.combine(selected_date, time(12, 0, 0)))
    afternoon_start_time = timezone.make_aware(datetime.combine(selected_date, time(13, 0, 0)))
    afternoon_end_time = timezone.make_aware(datetime.combine(selected_date, time(18, 0, 0)))

    # 计算工作总时长
    morning_duration = (morning_end_time - morning_start_time).total_seconds()
    afternoon_duration = (afternoon_end_time - afternoon_start_time).total_seconds()
    total_work_duration = morning_duration + afternoon_duration

    # 订单重量信息
    # 获取所有订单
    orders = Order.objects.all()

    # 创建一个字典来存储每个月的总重量
    monthly_weights = {}

    for order in orders:
        # 解析订单的开始日期
        order_date = datetime.strptime(order.order_start_date, '%Y-%m-%d')
        month_str = order_date.strftime('%Y-%m')  # 获取订单的年月

        # 初始化该月份的重量为0
        if month_str not in monthly_weights:
            monthly_weights[month_str] = 0

        # 获取该订单的所有 OrderProduct 记录
        order_products = OrderProduct.objects.filter(order=order)

        # 计算该订单的总重量
        order_weight = 0
        for order_product in order_products:
            # 获取产品的重量
            try:
                product = Product.objects.get(product_code=order_product.product_code)
                raw_code = product.raw_code
                raw = Raw.objects.get(raw_code=raw_code)
                raw_weight = raw.raw_weight
            except Product.DoesNotExist:
                raw_weight = 0  # 如果产品未找到，则重量为 0

            # 计算该产品的总重量并累加
            order_weight += round(float(raw_weight) * int(order_product.product_num_todo), 2)

        # 累加到该月的总重量
        monthly_weights[month_str] += round(order_weight, 2)

    # 将结果转换为按月份排序的列表
    months = sorted(monthly_weights.keys())
    weights = [round(monthly_weights[month], 2) for month in months]

    # 设备负载信息
    devices = Device.objects.all()
    device_details = []

    for device in devices:
        # 过滤出指定日期和工作时间范围内的任务
        tasks = Task.objects.filter(
            device_name=device.device_name,
            task_start_time__gte=morning_start_time,
            task_end_time__lte=afternoon_end_time
        )

        total_task_time = 0

        for task in tasks:
            # 计算每个任务的有效工作时间（考虑分段工作时间）
            task_start = max(task.task_start_time, morning_start_time)
            task_end = min(task.task_end_time, afternoon_end_time)

            if task_start < morning_end_time:
                morning_task_time = min(task_end, morning_end_time) - task_start
            else:
                morning_task_time = timedelta(0)

            if task_end > afternoon_start_time:
                afternoon_task_time = task_end - max(task_start, afternoon_start_time)
            else:
                afternoon_task_time = timedelta(0)

            total_task_time += morning_task_time.total_seconds() + afternoon_task_time.total_seconds()

        # 计算设备负载情况
        load_percentage = (total_task_time / total_work_duration) * 100 if total_work_duration > 0 else 0

        device_details.append({
            'device_name': device.device_name,
            'operator': device.operator.username if device.operator else 'N/A',
            'inspector': device.inspector.username if device.inspector else 'N/A',
            'load_percentage': load_percentage
        })

    # 获取当前日期
    current_date = timezone.now().date()

    # 获取两周后的日期
    two_weeks_later = current_date + timedelta(weeks=2)

    # 查询所有交付日期在两周之内的订单
    orders = Order.objects.filter(
        order_end_date__gte=current_date,
        order_end_date__lte=two_weeks_later
    )

    # 获取所有订单
    orders = Order.objects.all()

    # 存储订单详情
    order_details_combined = []

    for order in orders:
        end_date = datetime.strptime(order.order_end_date, '%Y-%m-%d').date()
        remaining_days = (end_date - current_date).days

        # 查找该订单的所有 task，按 task_end_time 排序并获取最后一个 task
        tasks = Task.objects.filter(order_code=order.order_code).order_by('-task_end_time')

        if tasks.exists():
            estimated_delivery_date = tasks.first().task_end_time
        else:
            estimated_delivery_date = None

        # 将订单详情添加到列表中
        order_details_combined.append({
            'order_code': order.order_code,
            'end_date': order.order_end_date,
            'remaining_days': remaining_days,
            'estimated_delivery_date': estimated_delivery_date,
        })

    # 按剩余天数从低到高排序
    order_details_combined.sort(key=lambda x: x['remaining_days'])

    # 统计每个毛坯编码的总数量
    raw_quantities = Raw.objects.values('raw_code', 'raw_name').annotate(total_quantity=Sum('raw_num'))

    # 统计所有 process_i 为 1 的任务中对应毛坯的消耗数量
    task_consumptions = Task.objects.filter(process_i=1).values('product_code').annotate(
        total_consumption=Sum('product_num')
    )

    # 创建一个字典来存储每个 raw_code 的消耗数量
    consumption_dict = {}

    for task in task_consumptions:
        # 获取对应的产品
        product_code = task['product_code']
        product = Product.objects.filter(product_code=product_code).first()
        if product and product.raw_code:
            # 将产品的消耗数量加到对应的 raw_code 上
            raw_code = product.raw_code
            if raw_code in consumption_dict:
                consumption_dict[raw_code] += task['total_consumption']
            else:
                consumption_dict[raw_code] = task['total_consumption']

    # 计算剩余数量并排序
    remaining_quantities = []
    for raw in raw_quantities:
        raw_code = raw['raw_code']
        raw_name = raw['raw_name']
        total_quantity = raw['total_quantity']
        consumed_quantity = consumption_dict.get(raw_code, 0)
        remaining_quantity = total_quantity - consumed_quantity

        remaining_quantities.append({
            'raw_code': raw_code,
            'raw_name': raw_name,
            'remaining_quantity': remaining_quantity
        })

    # 按剩余数量从低到高排序
    remaining_quantities.sort(key=lambda x: x['remaining_quantity'])

    context = {
        'segment': 'index',
        'orders_count': orders_count,
        'products_count': products_count,
        'raw_count': raw_count,
        'exchange_count': exchange_count,
        'process_count': process_count,
        'weight': 10,
        'start_dates': start_dates,
        'start_date_counts': start_date_counts_list,
        'end_dates': end_dates,
        'end_date_counts': end_date_counts_list,

        'months': months,
        'weights': weights,

        'device_details': device_details,

        'order_details_combined': order_details_combined,

        'remaining_quantities': remaining_quantities[:29],
    }

    html_template = loader.get_template('home/index.html')
    return HttpResponse(html_template.render(context, request))


@login_required(login_url="/login/")
def pages(request):
    context = {}
    # All resource paths end in .html.
    # Pick out the html file name from the url. And load that template.
    try:

        load_template = request.path.split('/')[-1]

        if load_template == 'admin':
            return HttpResponseRedirect(reverse('admin:index'))
        context['segment'] = load_template
        logger.info(f"{load_template=}")
        html_template = loader.get_template('home/' + load_template)
        return HttpResponse(html_template.render(context, request))

    except template.TemplateDoesNotExist:

        html_template = loader.get_template('home/page-404.html')
        return HttpResponse(html_template.render(context, request))

    except:
        html_template = loader.get_template('home/page-500.html')
        return HttpResponse(html_template.render(context, request))


@login_required(login_url="/login/")
def _upload(request):
    if request.method == 'POST' and request.FILES.get('file'):
        uploaded_file = request.FILES['file']
        file_type = request.POST.get('file_type')
        fs = FileSystemStorage()
        filename = fs.save(uploaded_file.name, uploaded_file)

        file_path = os.path.join(fs.location, filename)

        try:
            if file_type == 'orders':
                preprocess_order(file_path)
            elif file_type == 'raw':
                preprocess_raw(file_path)
            elif file_type == 'process':
                preprocess_process(file_path)
            elif file_type == 'product':
                preprocess_product(file_path)
            elif file_type == 'exchange':
                preprocess_device(file_path)
            else:
                return JsonResponse({"error": "Unknown file type."}, status=400)
        except Exception as e:
            return JsonResponse({"error": f"Error processing file: {str(e)}"}, status=500)

        return JsonResponse({"message": "File uploaded and processed successfully."})

    return render(request, 'home/index.html')


@csrf_exempt
@login_required(login_url="/login/")
def upload(request):
    if request.method == 'POST':
        try:
            file_type = request.POST.get('file_type')
            file = request.FILES['file']
            fs = FileSystemStorage()
            filename = fs.save(file.name, file)
            file_path = os.path.join(fs.location, filename)

            if file_type == 'orders':
                preprocess_order(file_path)
            elif file_type == 'raw':
                preprocess_raw(file_path)
            elif file_type == 'process':
                preprocess_process(file_path)
            elif file_type == 'product':
                preprocess_product(file_path)
            elif file_type == 'exchange':
                preprocess_device(file_path)
            else:
                raise ValueError("Invalid file type.")

            return JsonResponse({'status': 'success'})
        except Exception as e:
            logger.error(str(e), exc_info=True)  # 记录详细的错误信息
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method'})


from collections import defaultdict

@login_required(login_url="/login/")
def order_list(request):
    search_query = request.GET.get('search', '')  # 获取用户输入的搜索关键词

    # 如果存在搜索关键词，过滤订单产品列表
    if search_query:
        order_products = OrderProduct.objects.select_related('order').filter(
            Q(order__order_code__icontains=search_query) |
            Q(product_code__icontains=search_query) |
            Q(product_kind__icontains=search_query)
        ).order_by('order__order_start_date')
    else:
        # 如果没有搜索关键词，则获取所有订单产品
        order_products = OrderProduct.objects.select_related('order').all().order_by('order__order_start_date')

    per_page = request.GET.get('per_page', 50)  # 获取用户自定义的每页数量
    paginator = Paginator(order_products, per_page)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 获取当前页面的所有 product_code
    product_codes = [op.product_code for op in page_obj]

    # 仅获取当前页面需要的 Products
    products = Product.objects.filter(product_code__in=product_codes)
    product_dict = {product.product_code: product for product in products}

    # 获取这些 Products 所涉及到的所有 raw_code
    raw_codes = [product.raw_code for product in products if product.raw_code]
    raws = Raw.objects.filter(raw_code__in=raw_codes)
    raw_dict = {raw.raw_code: raw for raw in raws}

    # 使用 defaultdict 来跟踪每个 raw_code 的累计使用量
    raw_usage = defaultdict(int)  # key: raw_code, value: total product_num_todo used so far

    # 处理产品列表，添加原料代码和剩余原料数量
    orders_content = []
    for order_product in page_obj:
        product_code = order_product.product_code
        product_num_todo = order_product.product_num_todo

        # 获取对应的 Product 对象
        product = product_dict.get(product_code)
        if product:
            raw_code = product.raw_code
        else:
            raw_code = None

        # 获取对应的 Raw 对象并计算剩余原料数量
        if raw_code:
            raw = raw_dict.get(raw_code)
            if raw:
                initial_raw_num = raw.raw_num  # 初始的原料数量

                # 累计使用量：之前的使用量加上当前的 product_num_todo
                used_raw_num = raw_usage[raw_code] + product_num_todo

                # 计算剩余原料数量
                remain_raw_num = initial_raw_num - used_raw_num

                # 更新累计使用量
                raw_usage[raw_code] = used_raw_num
            else:
                remain_raw_num = 0
        else:
            remain_raw_num = 0

        orders_content.append({
            'order_code': order_product.order.order_code,
            'order_start_date': order_product.order.order_start_date,
            'order_end_date': order_product.order.order_end_date,
            'product_code': order_product.product_code,
            'product_kind': order_product.product_kind,
            'product_num_todo': product_num_todo,
            'product_num_done': order_product.product_num_done,
            'product_num_total': product_num_todo + order_product.product_num_done,
            'is_done': order_product.is_done,
            'remain_raw_num': remain_raw_num  # 添加剩余原料数量
        })

    context = {
        'orders_content': orders_content,
        'page_obj': page_obj,
        'per_page': per_page,
        'search_query': search_query
    }
    return render(request, 'home/order_list.html', context)

@login_required(login_url="/login/")
def get_order(request, order_id):
    order = get_object_or_404(Order, order_id=order_id)
    data = {
        'order_id': order.order_id,
        'order_date': order.order_start_date,
        'customer': order.customer,
        'sale_amount': order.sale_amount,
        'order_state': order.order_state,
    }
    return JsonResponse(data)


@login_required(login_url="/login/")
def update_order(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, order_id=order_id)
        order.order_start_date = request.POST.get('order_date')
        order.customer = request.POST.get('customer')
        order.sale_amount = request.POST.get('sale_amount')
        order.order_state = request.POST.get('order_state')
        order.save()
        return HttpResponse(status=200)
    return HttpResponse(status=400)


@csrf_exempt
def create_order(request):
    order_data = request.POST
    order_code = order_data.get('orderCode').strip()
    order_start_date = request.POST.get('orderStartDate', None)
    order_end_date = request.POST.get('orderEndDate', None)
    product_code = request.POST.get('productCode', '').strip()
    product_num_todo = request.POST.get('productNumTodo', 0)
    product_num_done = request.POST.get('productNumDone', 0)
    is_done = request.POST.get('isDone') == 'on'
    # 检查所有必要的字段是否都存在
    required_fields = ['orderCode', 'orderStartDate', 'orderEndDate', 'productCode', 'productNumTodo', 'productNumDone',
                       'isDone']
    # if not all(field in order_data for field in required_fields):
    #     return JsonResponse({
    #         'success': False,
    #         'message': '请求缺少必要的字段。'
    #     }, status=400)
    try:
        # 检查日期字符串是否为 None 或空字符串
        if order_start_date is None or order_start_date.strip() == '':
            return JsonResponse({
                'success': False,
                'message': '订单开始日期是必填项。'
            }, status=400)

        if order_end_date is None or order_end_date.strip() == '':
            return JsonResponse({
                'success': False,
                'message': '订单结束日期是必填项。'
            }, status=400)

        # 现在可以安全地解析日期字符串，因为已经检查过它们不为 None 且不为空
        order_start_date = datetime.strptime(order_start_date, '%Y-%m-%d').date()
        order_end_date = datetime.strptime(order_end_date, '%Y-%m-%d').date()

        # 确保 product_num_todo 和 product_num_done 是整数
        product_num_todo = int(product_num_todo)
        product_num_done = int(product_num_done)

    except Exception as e:
        raise (e)

    # 创建订单对象
    order = Order.objects.create(
        order_code=order_code,
        order_start_date=order_start_date,
        order_end_date=order_end_date
        # 假设 Order 模型还有其它字段，也在这里设置
    )

    # 创建订单产品对象
    order_product = OrderProduct.objects.create(
        order=order,
        product_code=product_code,
        product_num_todo=product_num_todo,
        product_num_done=product_num_done,
        is_done=is_done
    )

    # 返回成功的响应
    return JsonResponse({
        'success': True,
        'message': '订单创建成功。',
    }
    )


@login_required(login_url="/login/")
def delete_order(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, order_id=order_id)
        OrderProduct.objects.filter(order=order).delete()  # 删除相关的 OrderProduct
        order.delete()
        return HttpResponse(status=200)
    return HttpResponse(status=400)


@login_required(login_url="/login/")
def device_list(request):
    search_query = request.GET.get('search', '')  # 获取用户输入的搜索关键词

    # 如果存在搜索关键词，过滤设备列表
    if search_query:
        devices = Device.objects.filter(
            Q(device_name__icontains=search_query)
        )
    else:
        # 如果没有搜索关键词，则获取所有设备
        devices = Device.objects.all()

    return render(request, 'home/device_list.html', {'devices': devices})


@login_required(login_url="/login/")
def get_device(request, device_id):
    device = get_object_or_404(Device, id=device_id)
    operators = CustomUser.objects.filter(role='operator')  # 假设用户模型有角色字段
    inspectors = CustomUser.objects.filter(role='inspector')

    data = {
        'device_name': device.device_name,
        'exchange_time': device.changeover_time,
        'status': device.is_fault,
        'operator': device.operator.id if device.operator else None,
        'inspector': device.inspector.id if device.inspector else None,
        'operators': [{'id': op.id, 'name': op.username} for op in operators],
        'inspectors': [{'id': ins.id, 'name': ins.username} for ins in inspectors],
    }
    return JsonResponse(data)


@login_required(login_url="/login/")
def update_device(request, device_id):
    if request.method == 'POST':
        device = get_object_or_404(Device, id=device_id)
        device.device_name = request.POST.get('device_name')
        device.exchange_time = request.POST.get('exchange_time')
        device.is_fault = request.POST.get('status') == '1'
        device.operator_id = request.POST.get('operator')
        device.inspector_id = request.POST.get('inspector')
        device.save()
        return HttpResponse(status=200)
    return HttpResponse(status=400)


@login_required(login_url="/login/")
def delete_device(request, device_id):
    if request.method == 'POST':
        device = get_object_or_404(Device, id=device_id)
        device.delete()
        return HttpResponse(status=200)
    return HttpResponse(status=400)


@login_required(login_url="/login/")
def product_list(request):
    search_query = request.GET.get('search', '')  # 获取用户输入的搜索关键词
    per_page = request.GET.get('per_page', 50)  # 获取用户自定义的每页数量，默认为20

    # 如果存在搜索关键词，过滤产品列表
    if search_query:
        products = Product.objects.filter(
            Q(product_code__icontains=search_query) |
            Q(product_name__icontains=search_query)
        )
    else:
        products = Product.objects.all()

    paginator = Paginator(products, per_page)

    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 处理产品列表，添加原料代码
    product_list = []
    for product in page_obj:
        if product.raw_code:
            raws = Raw.objects.filter(raw_code=product.raw_code)
            if raws.exists():
                raw = raws.first()
                raw_weight = raw.raw_weight
            else:
                raw_weight = None  # 或者设置一个默认值
        else:
            raw_weight = None  # 或者设置一个默认值
        product_list.append({
            'product_code': product.product_code,
            'product_name': product.product_name,
            'product_kind': product.product_kind,
            'raw_code': product.raw_code,
            'weight': product.weight,
            'raw_weight': raw_weight
        })

    context = {
        'products': product_list,
        'page_obj': page_obj,
        'per_page': per_page
    }
    return render(request, 'home/product_list.html', context)


@login_required(login_url="/login/")
def get_product(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    data = {
        'product_code': product.product_code,
        'product_category': product.product_category,
        'raw': product.raw_code.raw_code if product.raw_code else None,
    }
    return JsonResponse(data)


@login_required(login_url="/login/")
def update_product(request, product_id):
    if request.method == 'POST':
        product = get_object_or_404(Product, id=product_id)
        product.product_code = request.POST.get('product_code')
        product.product_category = request.POST.get('product_category')
        raw_code = request.POST.get('raw')
        if raw_code:
            product.raw_code = get_object_or_404(Raw, raw_code=raw_code)
        else:
            product.raw_code = None
        product.save()
        return HttpResponse(status=200)
    return HttpResponse(status=400)


@login_required(login_url="/login/")
def delete_product(request, product_id):
    if request.method == 'POST':
        product = get_object_or_404(Product, id=product_id)
        product.delete()
        return HttpResponse(status=200)
    return HttpResponse(status=400)


@login_required(login_url="/login/")
def raw_list(request):
    search_query = request.GET.get('search', '')  # 获取用户输入的搜索关键词
    per_page = request.GET.get('per_page', 50)  # 获取用户自定义的每页数量，默认为50

    # 如果存在搜索关键词，过滤毛坯列表
    if search_query:
        raws = Raw.objects.filter(
            Q(raw_code__icontains=search_query) |
            Q(raw_name__icontains=search_query)
        ).values('raw_code', 'raw_name', 'raw_weight').annotate(
            raw_num=Sum('raw_num')
        ).order_by('raw_name')
    else:
        # 如果没有搜索关键词，则获取所有毛坯，并按名称合并
        raws = Raw.objects.values('raw_code', 'raw_name', 'raw_weight').annotate(
            raw_num=Sum('raw_num')
        ).order_by('raw_name')

    # 分页处理
    paginator = Paginator(raws, per_page)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 传递数据给模板
    context = {
        'raws': page_obj,  # 使用分页后的对象
        'page_obj': page_obj,  # 传递分页对象给模板
        'per_page': per_page,  # 传递每页数量给模板
    }
    return render(request, 'home/raw_list.html', context)


@login_required(login_url="/login/")
def raw_get(request, pk):
    raw = get_object_or_404(Raw, pk=pk)
    data = {
        'raw_code': raw.raw_code,
        'raw_name': raw.raw_name,
    }
    return JsonResponse(data)


@login_required(login_url="/login/")
def raw_update(request, pk):
    raw = get_object_or_404(Raw, pk=pk)
    if request.method == 'POST':
        raw.raw_code = request.POST.get('raw_code')
        raw.raw_name = request.POST.get('raw_name')
        raw.save()
        return JsonResponse({'success': True})


@login_required(login_url="/login/")
def raw_delete(request, pk):
    raw = get_object_or_404(Raw, pk=pk)
    if request.method == 'POST':
        raw.delete()
        return JsonResponse({'success': True})


@login_required(login_url="/login/")
def result_list(request):
    results = Task.objects.all()

    # 为每个 task 对象添加对应的 product_name, customer_name 和 product_kind
    for result in results:
        # 获取 Product 对象，添加 product_name
        product = Product.objects.filter(product_code=result.product_code).first()
        result.product_name = product.product_name if product else '⚠️ 未知产品'

        # 获取 OrderProduct 对象
        order_product = OrderProduct.objects.filter(product_code=result.product_code).first()
        if order_product:
            # 添加 customer_name 和 product_kind
            if order_product.order:
                result.customer_name = order_product.order.order_custom_name
                result.product_kind = order_product.product_kind
            else:
                result.customer_name = '⚠️ 未知客户 2'
                result.product_kind = '⚠️ 未知类别 2'
        else:
            result.customer_name = '⚠️ 未知客户 1 '
            result.product_kind = '⚠️ 未知类别 1 '

    # 分页处理
    per_page = request.GET.get('per_page', 50)  # 获取用户自定义的每页数量，默认为50
    paginator = Paginator(results, per_page)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'home/result_list.html', {'results': page_obj, 'page_obj': page_obj, 'per_page': per_page})


@login_required(login_url="/login/")
def get_progress(request):
    try:
        with open('./progress.txt', 'r') as f:
            progress = f.read()
            progress = "{:.1f}".format(float(progress))
    except FileNotFoundError:
        progress = '0'
    return JsonResponse({'progress': progress})


@login_required(login_url="/login/")
def delete_result(request, result_id):
    if request.method == 'POST':
        result = Task.objects.get(id=result_id)
        result.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})


@login_required(login_url="/login/")
def process_schedule_fast(request):
    from .job_scheduler import schedule_production
    if request.method == 'POST':
        Task.objects.all().delete()  # 清空 OrderProcessingResult 表
        schedule_production(fast=True)  # 重新计算排产结果
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})


@login_required(login_url="/login/")
def process_schedule(request):
    from .job_scheduler import schedule_production
    if request.method == 'POST':
        Task.objects.all().delete()  # 清空 OrderProcessingResult 表
        schedule_production()  # 重新计算排产结果
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})


@login_required(login_url="/login/")
def clear_schedule(request):
    if request.method == 'POST':
        Task.objects.all().delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False})


@login_required(login_url="/login/")
@csrf_exempt
def filter_by_date(request):
    if request.method == 'GET':
        date_str = request.GET.get('date', '2023-05-01')
        selected_date = parse_date(date_str)
        if selected_date:
            # Convert to local timezone if needed
            local_tz = pytz.timezone('Asia/Shanghai')  # Replace with your local timezone
            start_datetime = local_tz.localize(datetime.combine(selected_date, datetime.min.time()))
            end_datetime = start_datetime + timedelta(days=1)

            # Filter results within the selected date
            results = Task.objects.filter(
                execution_time__range=(start_datetime, end_datetime)
            ).values()

            return JsonResponse({'results': list(results)})
        return JsonResponse({'results': []})


@login_required(login_url="/login/")
def mark_complete(request, id):
    task = Task.objects.get(pk=id)
    task.completed = True
    task.save()
    return JsonResponse({'success': True})


@login_required(login_url="/login/")
def mark_not_complete(request, id):
    task = Task.objects.get(pk=id)
    if task:
        task.completed = False
        task.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)


@login_required(login_url="/login/")
def mark_inspected(request, id):
    task = Task.objects.get(pk=id)
    task.inspected = True
    task.save()
    return JsonResponse({'success': True})


@login_required(login_url="/login/")
def mark_not_inspected(request, id):
    task = Task.objects.get(pk=id)
    if task:
        task.inspected = False
        task.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)


@require_POST
@csrf_exempt
@login_required(login_url="/login/")
def add_urgent_task(request):
    task_start_time = request.POST.get('task_start_time')
    task_end_time = request.POST.get('task_end_time')
    order_code = request.POST.get('order_code')
    product_code = request.POST.get('product_code')
    process_i = request.POST.get('process_i')
    process_name = request.POST.get('process_name')
    device_name = request.POST.get('device_name')

    task = Task.objects.create(
        task_start_time=task_start_time,
        task_end_time=task_end_time,
        order_code=order_code,
        product_code=product_code,
        process_i=process_i,
        process_name=process_name,
        device_name=device_name,
        completed=False,
        inspected=False
    )

    return JsonResponse({'success': True, 'task_id': task.id})


# My tasks


@login_required(login_url="/login/")
def my_tasks(request):
    user = request.user

    # 获取用户关联的设备列表
    if user.role == 'admin':
        devices = Device.objects.all()
        tasks = Task.objects.filter(
            Q(completed=False) | Q(inspected=False)
        ).order_by('task_start_time')
    elif user.role == 'inspector':
        devices = Device.objects.filter(inspector=user)
        related_device_names = devices.values_list('device_name', flat=True)
        tasks = Task.objects.filter(
            device_name__in=related_device_names,
            inspected=False,
        ).order_by('task_start_time')
    else:
        devices = Device.objects.filter(operator=user)
        related_device_names = devices.values_list('device_name', flat=True)
        tasks = Task.objects.filter(
            device_name__in=related_device_names,
            completed=False,
        ).order_by('task_start_time')

    # 筛选任务
    selected_device = request.GET.get('device')
    if selected_device:
        tasks = tasks.filter(device_name=selected_device)

    for task in tasks:
        order_product = OrderProduct.objects.filter(product_code=task.product_code).first()
        task.customer_name = order_product.order.order_custom_name if order_product else '未知客户'
        product = Product.objects.filter(product_code=task.product_code).first()
        task.product_name = product.product_name if product else '⚠️ 未知产品'

    # 分页处理
    per_page = request.GET.get('per_page', 50)  # 获取用户自定义的每页数量，默认为50
    paginator = Paginator(tasks, per_page)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # 生成二维码
    current_url = request.build_absolute_uri()
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(current_url)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')

    img_path = 'apps/static/assets/img/qrcode/my_tasks_qr.png'
    os.makedirs(os.path.dirname(img_path), exist_ok=True)
    img.save(img_path)

    context = {
        'tasks': page_obj,
        'devices': devices,
        'selected_device': selected_device,
        'user': user,
        'qr_code_url': img_path,
        'page_obj': page_obj,
        'per_page': per_page,
    }
    return render(request, 'home/my_tasks.html', context)


@login_required(login_url="/login/")
def my_tasks_done(request):
    user = request.user

    # 获取用户关联的设备列表
    if user.role == 'admin':
        devices = Device.objects.all()
        tasks = Task.objects.filter(
            completed=True,
            inspected=True
        ).order_by('task_start_time')
    elif user.role == 'inspector':
        devices = Device.objects.filter(inspector=user)
        related_device_names = devices.values_list('device_name', flat=True)
        tasks = Task.objects.filter(
            device_name__in=related_device_names,
            inspected=True,
        ).order_by('task_start_time')
    else:
        devices = Device.objects.filter(operator=user)
        related_device_names = devices.values_list('device_name', flat=True)
        tasks = Task.objects.filter(
            device_name__in=related_device_names,
            completed=True,
        ).order_by('task_start_time')

    # 筛选任务
    selected_device = request.GET.get('device')
    if selected_device:
        tasks = tasks.filter(device_name=selected_device)

    for task in tasks:
        order_product = OrderProduct.objects.filter(product_code=task.product_code).first()
        task.customer_name = order_product.order.order_custom_name if order_product else '未知客户'
        product = Product.objects.filter(product_code=task.product_code).first()
        task.product_name = product.product_name if product else '⚠️ 未知产品'

    # 生成二维码
    current_url = request.build_absolute_uri()
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(current_url)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')

    img_path = 'apps/static/assets/img/qrcode/my_tasks_done_qr.png'
    os.makedirs(os.path.dirname(img_path), exist_ok=True)
    img.save(img_path)

    context = {
        'tasks': tasks,
        'devices': devices,
        'selected_device': selected_device,
        'user': user,
        'qr_code_url': img_path,
    }
    return render(request, 'home/my_tasks.html', context)


@login_required(login_url="/login/")
def my_tasks_operator_detail(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    return render(request, 'home/my_tasks_operator_detail.html', {'task': task})


def is_max_process(order_product):
    """
    判断当前工序是否是订单产品的最大工序。
    """
    all_processes = Process.objects.filter(product_code=order_product.product_code).order_by('process_i')
    process_indices = [p.process_i for p in all_processes]
    max_process_i = max(process_indices)
    return order_product.cur_process_i == max_process_i


@csrf_exempt
@login_required(login_url="/login/")
def my_tasks_operator_complete_task(request):
    if request.method == 'POST':
        # Update OrderProduct
        task_id = request.POST.get('task_id')
        product_num = request.POST.get('product_num')

        task = Task.objects.get(id=task_id)
        order_code = task.order_code
        order = get_object_or_404(Order, order_code=order_code)

        product_code = task.product_code
        order_product = OrderProduct.objects.filter(product_code=product_code, order=order).last()
        task.product_num_completed += int(product_num)
        if task.product_num_completed >= task.product_num:
            task.completed = 1

        task.save()

        if is_max_process(order_product):
            order_product.product_num_done += product_num
            order_product.product_num_todo -= product_num
            if order_product.product_num_todo <= 0:
                order_product.is_done = 1
            order_product.save()

            # Update Order
            order = order_product.order
            if not OrderProduct.objects.filter(order=order, is_done=0).exists():
                order.is_done = 1
            order.save()

            # Update Weight
            product = Product.objects.get(code=order_product.product_code)
            weight = Weight.objects.get(product=product)
            weight.weight += product.weight * product_num
            weight.save()

        return JsonResponse({'success': True,
                             'product_num': task.product_num, })
    return JsonResponse({'success': False})


@csrf_exempt
@login_required(login_url="/login/")
def my_tasks_operator_rework_task(request):
    if request.method == 'POST':
        task_id = request.POST.get('task_id')

        rework_product_num = int(request.POST.get('product_num_2'))

        try:
            # Get the OrderProduct instance
            task = Task.objects.get(id=task_id)
            task.product_num_completed -= rework_product_num
            task.product_num_inspected -= rework_product_num
            task.completed = 0
            task.inspected = 0
            task.save()
            order_code = task.order_code
            order = get_object_or_404(Order, order_code=order_code)

            product_code = task.product_code
            order_product = OrderProduct.objects.filter(product_code=product_code, order=order).last()

            # Decrement cur_process_i
            if order_product.cur_process_i > 0:
                order_product.cur_process_i -= 1
                order_product.save()

            return JsonResponse({'success': True})
        except OrderProduct.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'OrderProduct not found'})

    return JsonResponse({'success': False})


@csrf_exempt
@login_required(login_url="/login/")
def my_tasks_operator_scrap_task(request):
    if request.method == 'POST':
        task_id = request.POST.get('task_id')
        scrap_product_num = int(request.POST.get('product_num_3'))

        try:
            # Get the OrderProduct instance
            task = Task.objects.get(id=task_id)
            task.product_num -= scrap_product_num
            task.completed = 0
            task.inspected = 0
            order_code = task.order_code
            order = get_object_or_404(Order, order_code=order_code)

            OrderProduct.objects.create(
                order=order,
                product_code=task.product_code,
                product_num_todo=scrap_product_num
            )

            task.save()

            product_code = task.product_code
            order_product = OrderProduct.objects.filter(product_code=product_code, order=order).last()

            # Set cur_process_i to 0
            order_product.cur_process_i = 0
            order_product.save()

            return JsonResponse({'success': True})
        except OrderProduct.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'OrderProduct not found'})

    return JsonResponse({'success': False})


@login_required(login_url="/login/")
def my_tasks_inspector_detail(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    return render(request, 'home/my_tasks_inspector_detail.html', {'task': task})


@csrf_exempt
def my_tasks_inspector_complete_task(request):
    if request.method == 'POST':
        # Update OrderProduct
        task_id = request.POST.get('task_id')
        num_inspected = int(request.POST.get('product_num'))
        task = Task.objects.get(id=task_id)
        task.product_num_inspected += num_inspected
        if task.product_num_inspected >= task.product_num:
            task.inspected = 1
        task.save()

        return JsonResponse({'success': True,
                             'product_num': task.product_num, })
    return JsonResponse({'success': False})


@csrf_exempt
def my_tasks_inspector_complete_tasks(request):
    if request.method == 'POST':
        task_ids = request.POST.getlist('tasks[]')
        action = request.POST.get('action')

        if action == 'complete':
            tasks = Task.objects.filter(id__in=task_ids)
            for task in tasks:
                task.inspected = True
                task.product_num_inspected = task.product_num_completed
                task.save()
            return JsonResponse({'status': 'success', 'message': 'Tasks updated successfully'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Invalid action'})

    return JsonResponse({'status': 'error', 'message': 'Invalid request method'})


@csrf_exempt
@login_required(login_url="/login/")
def my_tasks_operator_one_btn_complete_tasks(request):
    if request.method == 'POST':
        task_ids = request.POST.getlist('tasks[]')

        for task_id in task_ids:
            task = Task.objects.get(id=task_id)
            order_code = task.order_code
            order = get_object_or_404(Order, order_code=order_code)

            product_code = task.product_code
            order_product = OrderProduct.objects.filter(product_code=product_code, order=order).last()
            task.product_num_completed = int(task.product_num)
            task.completed = 1

            task.save()

            if is_max_process(order_product):
                order_product.product_num_done += task.product_num
                order_product.product_num_todo -= task.product_num
                if order_product.product_num_todo <= 0:
                    order_product.is_done = 1
                order_product.save()

                # Update Order
                order = order_product.order
                if not OrderProduct.objects.filter(order=order, is_done=0).exists():
                    order.is_done = 1
                order.save()

                # Update Weight
                product = Product.objects.get(code=order_product.product_code)
                weight = Weight.objects.get(product=product)
                weight.weight += product.weight * task.product_num
                weight.save()

        return JsonResponse({'success': True,
                             'product_num': task.product_num, })
    return JsonResponse({'success': False})


@csrf_exempt
@login_required(login_url="/login/")
def my_tasks_inspector_scrap_tasks(request):
    if request.method == 'POST':
        task_ids = request.POST.getlist('tasks[]')

        try:
            for task_id in task_ids:
                # Get the Task instance
                task = Task.objects.get(id=task_id)
                scrap_product_num = task.product_num_completed
                task.product_num -= scrap_product_num
                task.completed = False
                task.inspected = False
                order_code = task.order_code
                order = get_object_or_404(Order, order_code=order_code)

                # Create a new OrderProduct entry for the scrapped items
                OrderProduct.objects.create(
                    order=order,
                    product_code=task.product_code,
                    product_num_todo=scrap_product_num
                )

                task.save()

                product_code = task.product_code
                order_product = OrderProduct.objects.filter(product_code=product_code, order=order).first()

                # Reset cur_process_i to 0
                order_product.cur_process_i = 0
                order_product.save()

            return JsonResponse({'success': True})
        except OrderProduct.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'OrderProduct not found'})

    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required(login_url="/login/")
def generate_pdf(request):
    # 上海时区
    shanghai_tz = pytz.timezone('Asia/Shanghai')

    # 获取当前用户及其角色
    user = request.user
    if user.role == 'admin':
        tasks = Task.objects.all().order_by('task_start_time')
    elif user.role == 'inspector':
        related_device_names = Device.objects.filter(inspector=user).values_list('device_name', flat=True)
        tasks = Task.objects.filter(
            device_name__in=related_device_names,
            completed=1,
        ).order_by('task_start_time')
    else:
        related_device_names = Device.objects.filter(operator=user).values_list('device_name', flat=True)
        tasks = Task.objects.filter(device_name__in=related_device_names).order_by('task_start_time')

    # 创建PDF缓冲区
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []

    # 使用 Source Han Sans CN 字体
    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle(name='Normal', fontName='SourceHanSansCN', fontSize=13)

    # 按设备名称对任务进行分组
    tasks_by_device = {}
    for task in tasks:
        device_name = task.device_name
        if device_name not in tasks_by_device:
            tasks_by_device[device_name] = []
        tasks_by_device[device_name].append(task)

    # 为每个设备创建一个表格
    for device_name, device_tasks in tasks_by_device.items():
        # 表格数据
        data = [['设备', '开始时间', '是否换型', '商品', '工序号', '工序名', '数量']]
        for task in device_tasks:
            start_time = task.task_start_time.astimezone(shanghai_tz)
            data.append([
                task.device_name,
                start_time.strftime('%m-%d %H:%M'),
                task.is_changeover,
                task.product_code,
                task.process_i,
                task.process_name,
                task.product_num
            ])

        # 创建表格并设置样式
        table = Table(data, colWidths=[doc.width / len(data[0])] * len(data[0]), repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), '#d0e0f0'),
            ('TEXTCOLOR', (0, 0), (-1, 0), '#000000'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'SourceHanSansCN'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), '#f5f5f5'),
            ('GRID', (0, 0), (-1, -1), 1, '#d0d0d0'),
        ]))

        # 添加设备名称作为标题
        elements.append(table)
        elements.append(Spacer(1, 20))  # 在标题和表格之间添加20个点的垂直间距
        elements.append(PageBreak())  # 每个设备的表格后添加分页

    # 构建文档
    doc.build(elements)

    # 设置响应内容类型为PDF
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="production_task_list.pdf"'

    return response


def add_qr_code(canvas, doc):
    # 添加二维码到页面右上角
    qr_code_path = 'apps/static/assets/img/qrcode/my_tasks_qr.png'
    qr_code_width = 50  # 设置二维码图片宽度
    qr_code_height = 50  # 设置二维码图片高度
    qr_code_x = doc.pagesize[0] - qr_code_width - inch  # 图片X坐标
    qr_code_y = doc.pagesize[1] - qr_code_height - inch  # 图片Y坐标
    canvas.drawImage(qr_code_path, qr_code_x, qr_code_y, width=qr_code_width, height=qr_code_height)


def schedule_by_date(request):
    # 获取查询参数 'date'
    date_str = request.GET.get('date')
    if not date_str:
        return JsonResponse({
            'success': False,
            'message': 'Missing date parameter'
        }, status=400)

    try:
        # 将字符串转换为日期对象
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        # 设置时区（根据需要设置为您的本地时区）
        local_tz = timezone.get_default_timezone()
        # 创建开始时间和结束时间
        start_datetime = timezone.make_aware(datetime.combine(selected_date, datetime.min.time()), local_tz)
        end_datetime = start_datetime + timedelta(days=1)

        # 查询数据库，获取指定日期的任务
        results = Task.objects.filter(
            task_start_time__range=(start_datetime, end_datetime)
        ).values('id', 'task_start_time', 'task_end_time', 'is_changeover', 'order_code', 'product_code', 'process_i',
                 'process_name', 'device_name', 'product_num', 'product_num_completed', 'product_num_inspected')

        # 检查是否有结果
        if results:
            return JsonResponse({
                'success': True,
                'results': list(results)  # 将查询结果转换为列表
            })
        else:
            return JsonResponse({
                'success': True,
                'message': 'No results found for the specified date',
                'results': []
            })

    except ValueError:
        # 日期格式错误
        return JsonResponse({
            'success': False,
            'message': 'Invalid date format, please use YYYY-MM-DD'
        }, status=400)


class ProcessListView(ListView):
    model = Process
    template_name = 'process_list.html'
    context_object_name = 'processes'
    paginate_by = 20  # 每页显示10个工序
    paginate_orphans = 5  # 避免最后一页只有少数几个对象

    def get_queryset(self):
        queryset = super().get_queryset()
        # 首先按商品编码进行排序
        queryset = queryset.order_by('product_code')
        # 然后按工序编号进行排序（如果商品编码相同）
        queryset = queryset.order_by('process_i')

        # 获取搜索查询参数
        search_query = self.request.GET.get('search', None)

        if search_query:
            # 如果提供了搜索查询，则过滤工序列表
            queryset = queryset.filter(
                Q(process_name__icontains=search_query) |
                Q(product_code__icontains=search_query)
            )

        queryset = queryset.order_by('product_code', 'process_i')

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('search', '')
        return context


class AddProcessView(View):
    def post(self, request):
        form = ProcessForm(request.POST)
        if form.is_valid():
            process = form.save(commit=False)
            # 这里可以添加额外的逻辑，例如设置用户等
            process.save()
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': form.errors})


@login_required(login_url="/login/")
def get_process(request, process_id):
    process = get_object_or_404(Process, id=process_id)

    data = {
        'process_name': process.process_name,
        'process_capacity': process.process_capacity,
        'process_duration': process.process_duration,
        'product_code': process.product_code,
        'device_name': process.device_name,
        'is_outside': process.is_outside,
        'is_last_process': process.is_last_process,
    }
    return JsonResponse(data)


@login_required(login_url="/login/")
def update_process(request, process_id):
    if request.method == 'POST':
        process = get_object_or_404(Process, id=process_id)
        process.process_name = request.POST.get('process_name')
        process.process_capacity = request.POST.get('process_capacity')
        process.process_duration = request.POST.get('process_duration')
        process.product_code = request.POST.get('product_code')
        process.device_name = request.POST.get('device_name')
        process.is_outside = request.POST.get('is_outside') == 'True'
        process.is_last_process = request.POST.get('is_last_process') == 'True'
        process.save()
        return HttpResponse(status=200)
    return HttpResponse(status=400)


def delete_process(request, process_id):
    if request.method == 'POST':
        process = get_object_or_404(Process, pk=process_id)
        process.delete()
        return JsonResponse({'success': True})
    else:
        # 如果不是POST请求，返回错误信息
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=405)
