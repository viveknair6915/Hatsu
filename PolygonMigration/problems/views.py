from django.shortcuts import render
from .polygon_api import PolygonAPI
from .models import Problem, SampleTestCase, ProblemTestCase, ProblemTag
from django.utils.text import slugify
from django.conf import settings
import re
import lxml.html
import logging
import json
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction

logger = logging.getLogger(__name__)

def parse_problem_html(html_content):
    if not html_content:
        return {
            'title': '',
            'legend': '',
            'input_format': '',
            'output_format': '',
            'notes': '',
        }

    try:
        tree = lxml.html.fromstring(html_content)
    except Exception:
        return {
            'title': '',
            'legend': '',
            'input_format': '',
            'output_format': '',
            'notes': '',
        }

    def get_div_inner_html(tree, class_name, skip_section_title=False):
        div = tree.xpath(f'//div[@class="{class_name}"]')
        if not div:
            return ''
        div = div[0]
        children = div.getchildren()
        html_parts = []
        for child in children:
            if skip_section_title and child.tag == 'div' and 'section-title' in child.get('class', ''):
                continue
            html_parts.append(lxml.html.tostring(child, encoding='unicode'))
        content = ''.join(html_parts).strip()
        content = re.sub(r'^(<p>\s*</p>)+', '', content)
        return content

    legend = get_div_inner_html(tree, 'legend')
    input_format = get_div_inner_html(tree, 'input-specification', skip_section_title=True)
    output_format = get_div_inner_html(tree, 'output-specification', skip_section_title=True)

    title = ''
    title_div = tree.xpath('//div[@class="title"]')
    if title_div:
        title = title_div[0].text_content().strip()

    notes = get_div_inner_html(tree, 'note', skip_section_title=True)

    return {
        'title': title,
        'legend': legend,
        'input_format': input_format,
        'output_format': output_format,
        'notes': notes,
    }

@user_passes_test(lambda u: u.is_authenticated and u.is_staff, login_url='/users/login/')
def index(request):
    context = {}
    
    all_tags = ProblemTag.objects.all().order_by('tag_name')
    context['all_tags'] = all_tags
    
    context['all_tags_json'] = json.dumps([{
        'pk': tag.id,
        'fields': {'name': tag.tag_name}
    } for tag in all_tags])
    
    if 'selected_tags' not in context:
        context['selected_tags'] = []
    context['selected_tags_json'] = json.dumps(context['selected_tags'])
    
    if request.method == 'POST':
        polygon_id = request.POST.get('problem_id', '').strip()
        migrate_to_azure = request.POST.get('migrate_to_azure')
        migrate_to_db = request.POST.get('migrate_to_db')
        migrate_test_cases_to_db = request.POST.get('migrate_test_cases_to_db')
        difficulty = request.POST.get('difficulty', '').strip()
        selected_tags = request.POST.getlist('tags')
        new_tag = request.POST.get('new_tag', '').strip()
        
        context['difficulty'] = difficulty
        context['selected_tags'] = selected_tags
        context['new_tag'] = new_tag
        
        if not polygon_id:
            context['error'] = 'Please enter a Polygon Problem ID.'
            return render(request, 'problems/index.html', context)

        context['problem_id'] = polygon_id
        db_problem = Problem.objects.filter(polygon_id=polygon_id).first()
        
        if db_problem:
            context['db_problem'] = db_problem
            context['selected_tags'] = [tag.tag_name for tag in db_problem.extra_tags.all()]
            context['difficulty'] = db_problem.difficulty

        api = PolygonAPI()

        if migrate_to_azure:
            if not db_problem:
                context['error'] = f"Problem with Polygon ID {polygon_id} has not been migrated to the database yet. Please migrate the problem to the database first before migrating test cases to Azure."
                return render(request, 'problems/index.html', context)

            try:
                from problems.storage import get_storage_service
                storage = get_storage_service()

                storage.delete_test_cases(db_problem.id)
                api.delete_problem_test_case_cache(db_problem.id)

                test_cases = api.get_test_cases_from_redis(polygon_id)
                if test_cases is None:
                    test_cases = api.get_all_test_cases(polygon_id)
                    api.store_test_cases_in_redis(polygon_id, test_cases, expiry_hours=0.5)

                uploaded_count = 0
                for idx, test in enumerate(test_cases, start=1):
                    in_data = test.get('input', '')
                    out_data = test.get('output', '')
                    if in_data and out_data:
                        storage.upload_test_case(db_problem.id, idx, in_data, out_data)
                        uploaded_count += 1

                custom_checker_info = api.get_custom_checker_info(polygon_id)
                if custom_checker_info:
                    try:
                        checker_code = api.fetch_custom_checker_file(polygon_id, custom_checker_info['name'])
                        if checker_code:
                            storage.upload_file(f"test_cases/{db_problem.id}/custom_checker.cpp", checker_code)
                    except Exception as checker_err:
                        logger.warning('Failed to upload custom checker to storage: %s', checker_err)

                provider_name = 'Azure Blob Storage' if storage.__class__.__name__ == 'AzureStorageService' else 'Local/Cloud Storage'
                success_message = f"Test cases migrated to {provider_name} successfully ({uploaded_count} test cases uploaded)."
                if custom_checker_info:
                    success_message += f" Custom checker '{custom_checker_info['name']}' uploaded."
                context['success'] = success_message
            except Exception as e:
                logger.error('Storage migration failed: %s', e, exc_info=True)
                context['error'] = f"Storage migration failed: {str(e)}"

        try:
            info = api.get_problem_info(polygon_id)
            if not info:
                info = {}
        except Exception as e:
            logger.error('Failed to get problem info: %s', e)
            context['error'] = f"Failed to fetch problem {polygon_id} from Polygon: {str(e)}"
            return render(request, 'problems/index.html', context)

        html_data = {'title': '', 'legend': '', 'input_format': '', 'output_format': '', 'notes': ''}
        try:
            problem_html_content = api.download_and_extract_package(polygon_id)
            html_data = parse_problem_html(problem_html_content)
        except Exception as e:
            logger.warning('Could not extract problem.html: %s. Using statements fallback.', e)
            try:
                statements = api.get_statements(polygon_id)
                if statements and isinstance(statements, dict):
                    first_stmt = next(iter(statements.values()))
                    html_data = {
                        'title': first_stmt.get('name', ''),
                        'legend': first_stmt.get('legend', ''),
                        'input_format': first_stmt.get('input', ''),
                        'output_format': first_stmt.get('output', ''),
                        'notes': first_stmt.get('notes', ''),
                    }
            except Exception as stmt_err:
                logger.warning('Statements API fallback failed: %s', stmt_err)

        all_test_cases = []
        try:
            if migrate_to_db or migrate_test_cases_to_db or migrate_to_azure:
                all_test_cases = api.get_test_cases_from_redis(polygon_id)
                if all_test_cases is None:
                    all_test_cases = api.get_all_test_cases(polygon_id)
                    api.store_test_cases_in_redis(polygon_id, all_test_cases, expiry_hours=0.5)
            else:
                all_test_cases = api.get_all_test_cases(polygon_id)
                api.store_test_cases_in_redis(polygon_id, all_test_cases, expiry_hours=0.5)
        except Exception as e:
            logger.error('Failed to fetch test cases: %s', e)
            if not context.get('error'):
                context['error'] = f"Failed to fetch test cases: {str(e)}"
            all_test_cases = []

        display_test_cases = []
        for test_case in all_test_cases:
            input_data = test_case.get('input', '')
            output_data = test_case.get('output', '')
            description = test_case.get('description', '')
            display_test_cases.append({
                'index': test_case.get('index', ''),
                'input_preview': input_data[:50] + ('...' if len(input_data) > 50 else ''),
                'output_preview': output_data[:50] + ('...' if len(output_data) > 50 else ''),
                'description_preview': description[:50] + ('...' if len(description) > 50 else ''),
                'is_sample': test_case.get('is_sample', False),
                'full_input': input_data,
                'full_output': output_data,
                'full_description': description,
            })
        context['all_test_cases'] = display_test_cases

        title = html_data.get('title') or info.get('name') or f"Polygon Problem {polygon_id}"
        base_slug = slugify(title)
        if not base_slug:
            base_slug = f"polygon-{polygon_id}"

        if Problem.objects.filter(slug=base_slug).exclude(polygon_id=polygon_id).exists():
            slug = f"{base_slug}-{polygon_id}"
        else:
            slug = base_slug

        problem_statement = html_data.get('legend', '')
        input_format = html_data.get('input_format', '')
        output_format = html_data.get('output_format', '')
        constraints = ''
        editorial = ''
        time_limit = info.get('timeLimit', 1000)
        memory_limit = info.get('memoryLimit', 256)

        try:
            checker_type = api._make_request('problem.checker', {'problemId': polygon_id}) or 'ncmp'
            if checker_type.startswith('std::'):
                checker_type = checker_type[5:]
            if checker_type.endswith('.cpp'):
                checker_type = checker_type[:-4]
            valid_checkers = ['ncmp', 'fcmp', 'hcmp', 'lcmp', 'nyesno', 'rcmp4', 'rcmp6', 'rcmp9', 'wcmp', 'yesno']
            if checker_type not in valid_checkers:
                checker_type = 'custom'
        except Exception:
            checker_type = 'ncmp'

        custom_checker_info = api.get_custom_checker_info(polygon_id)
        test_case_count = len(all_test_cases)
        notes = html_data.get('notes', '')

        context['fetched_problem'] = {
            'polygon_id': polygon_id,
            'title': title,
            'slug': slug,
            'difficulty': difficulty,
            'problem_statement': problem_statement,
            'input_format': input_format,
            'output_format': output_format,
            'constraints': constraints,
            'editorial': editorial,
            'time_limit': time_limit,
            'memory_limit': memory_limit,
            'checker_type': checker_type,
            'custom_checker_info': custom_checker_info,
            'test_case_count': test_case_count,
            'notes': notes,
        }

        main_solution = None
        try:
            solutions = api._make_request('problem.solutions', {'problemId': polygon_id})
            if solutions:
                main_solution_name = None
                for sol in solutions:
                    if sol.get('tag') == 'MA':
                        main_solution_name = sol['name']
                        break
                if not main_solution_name and solutions:
                    main_solution_name = solutions[0]['name']
                if main_solution_name:
                    main_solution = api._make_plain_request('problem.viewSolution', {
                        'problemId': polygon_id,
                        'name': main_solution_name
                    })
        except Exception as e:
            logger.error('Error fetching solution: %s', e)
        context['main_solution'] = main_solution

        if migrate_to_db:
            valid_difficulties = [c[0] for c in Problem.DIFFICULTY_CHOICES]
            if not difficulty or difficulty not in valid_difficulties:
                context['error'] = "Please select a valid difficulty level (easy, medium, or hard) before migrating to database."
                return render(request, 'problems/index.html', context)

            tags_list = [t.strip() for t in selected_tags if t.strip()]
            if new_tag and new_tag.strip():
                tags_list.append(new_tag.strip())
            unique_tags = list(dict.fromkeys(tags_list))

            if len(unique_tags) < 2:
                context['error'] = "Please select at least two tags before migrating to database."
                return render(request, 'problems/index.html', context)

            try:
                with transaction.atomic():
                    problem_obj = Problem.objects.filter(polygon_id=polygon_id).first()
                    if problem_obj:
                        problem_obj.title = title
                        problem_obj.slug = slug
                        problem_obj.difficulty = difficulty
                        problem_obj.problem_statement = problem_statement
                        problem_obj.input_format = input_format
                        problem_obj.output_format = output_format
                        problem_obj.constraints = constraints
                        problem_obj.editorial = editorial
                        problem_obj.time_limit = time_limit
                        problem_obj.memory_limit = memory_limit
                        problem_obj.checker_type = checker_type
                        problem_obj.test_case_count = test_case_count
                        problem_obj.notes = notes
                        problem_obj.save()
                        context['db_success'] = f"Problem '{title}' updated in database successfully."
                    else:
                        problem_obj = Problem.objects.create(
                            polygon_id=polygon_id,
                            title=title,
                            slug=slug,
                            difficulty=difficulty,
                            problem_statement=problem_statement,
                            input_format=input_format,
                            output_format=output_format,
                            constraints=constraints,
                            editorial=editorial,
                            time_limit=time_limit,
                            memory_limit=memory_limit,
                            checker_type=checker_type,
                            test_case_count=test_case_count,
                            notes=notes,
                        )
                        context['db_success'] = f"Problem '{title}' created in database successfully."

                    problem_obj.extra_tags.clear()
                    for tag_name in unique_tags:
                        tag_obj, _ = ProblemTag.objects.get_or_create(tag_name=tag_name)
                        problem_obj.extra_tags.add(tag_obj)

                    context['selected_tags'] = unique_tags

                    sample_tests = [test for test in all_test_cases if test.get('is_sample', False)]
                    existing_samples = list(SampleTestCase.objects.filter(problem=problem_obj).order_by('order'))
                    kept_sample_ids = []
                    order = 1
                    for idx, test in enumerate(sample_tests):
                        in_data = test.get('input', '')
                        out_data = test.get('output', '')
                        if in_data is not None and out_data is not None:
                            if idx < len(existing_samples):
                                stc = existing_samples[idx]
                                stc.input = in_data
                                stc.output = out_data
                                stc.order = order
                                stc.save()
                                kept_sample_ids.append(stc.id)
                            else:
                                stc = SampleTestCase.objects.create(
                                    problem=problem_obj,
                                    input=in_data,
                                    output=out_data,
                                    order=order
                                )
                                kept_sample_ids.append(stc.id)
                            order += 1

                    SampleTestCase.objects.filter(problem=problem_obj).exclude(id__in=kept_sample_ids).delete()

                    context['db_problem'] = problem_obj
                    if custom_checker_info:
                        context['db_success'] += f" Custom checker '{custom_checker_info['name']}' detected."
            except Exception as e:
                logger.error('DB migration failed: %s', e, exc_info=True)
                context['error'] = f"Database migration failed: {str(e)}"

        if migrate_test_cases_to_db:
            problem_obj = Problem.objects.filter(polygon_id=polygon_id).first()
            if not problem_obj:
                context['error'] = "Please migrate the problem to the database first."
                return render(request, 'problems/index.html', context)

            try:
                with transaction.atomic():
                    existing_test_cases = list(ProblemTestCase.objects.filter(problem=problem_obj).order_by('order'))
                    kept_test_ids = []
                    order = 1
                    for idx, test in enumerate(all_test_cases):
                        input_data = test.get('input', '')
                        output_data = test.get('output', '')
                        is_sample = test.get('is_sample', False)
                        description = test.get('description', '') or ''

                        if idx < len(existing_test_cases):
                            ptc = existing_test_cases[idx]
                            ptc.input = input_data
                            ptc.output = output_data
                            ptc.is_sample = is_sample
                            ptc.description = description
                            ptc.order = order
                            ptc.save()
                            kept_test_ids.append(ptc.id)
                        else:
                            ptc = ProblemTestCase.objects.create(
                                problem=problem_obj,
                                is_sample=is_sample,
                                input=input_data,
                                output=output_data,
                                description=description,
                                order=order
                            )
                            kept_test_ids.append(ptc.id)
                        order += 1

                    ProblemTestCase.objects.filter(problem=problem_obj).exclude(id__in=kept_test_ids).delete()
                    context['success'] = "Test cases migrated to database successfully."
            except Exception as e:
                logger.error('Test case DB migration failed: %s', e, exc_info=True)
                context['error'] = f"Test case database migration failed: {str(e)}"

    if 'selected_tags' in context:
        context['selected_tags_json'] = json.dumps(context['selected_tags'])
    else:
        context['selected_tags_json'] = json.dumps([])

    return render(request, 'problems/index.html', context)
