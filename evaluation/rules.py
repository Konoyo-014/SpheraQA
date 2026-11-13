"""Parse spatial answers and compare the requested quantities."""
from __future__ import annotations

import json

import math

import re

import unicodedata

THRESHOLDS = {'azimuth_deg':20.0, 'elevation_deg':10.0, 'distance_m':1.0}

TIME_TOLERANCE_S = 0.051  # released time values can be rounded to 0.1 s

NUM = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'

AXES = ('azimuth_deg','elevation_deg','distance_m')

MOVEMENT_AXES = ('lateral','distance_change','vertical')

SCALARS = {'azimuth':'azimuth_deg','elevation':'elevation_deg','distance':'distance_m'}

LABELS = {
    'left':r'left', 'right':r'right', 'front':r'front|ahead',
    'back':r'back|behind|rear', 'up':r'up|above', 'down':r'down|below',
    'near':r'near|close', 'far':r'far|distant',
}

CAT_AXES = {'cat_left_right':('left','right'),'cat_up_down':('up','down'),
            'cat_front_back':('front','back'),'cat_near_far':('near','far')}

class Invalid(ValueError):
    pass

def norm(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value)).replace('−','-')).strip().casefold()

def clean_question(question):
    return re.split(r'\bcoordinate\s+system\s*:',question,flags=re.I)[0].strip(' .?')

def answer_block(raw):
    if not isinstance(raw,str) or not raw.strip():raise Invalid('empty_response')
    text=raw.strip()
    if '<think>' in text:
        if '</think>' not in text:raise Invalid('unfinished_reasoning_without_answer')
        text=text.rsplit('</think>',1)[1].strip()
    answers=re.findall(r'<answer>(.*?)</answer>',text,re.S|re.I)
    if len(answers)>1:raise Invalid('multiple_answer_blocks')
    if answers:text=answers[0].strip()
    markers=list(re.finditer(r'(?:^|\n)\s*(?:final\s+answer|answer)\s*[:：]\s*',text,re.I))
    if markers:text=text[markers[-1].end():].strip()
    if text.startswith('```') and text.endswith('```'):
        text=re.sub(r'^```(?:json)?\s*','',text,flags=re.I).removesuffix('```').strip()
    text=text.replace('**','').strip()
    if not text:raise Invalid('empty_answer_block')
    return text

def number(value):
    if isinstance(value,bool):raise Invalid('boolean_is_not_numeric')
    try:value=float(value)
    except (TypeError,ValueError):raise Invalid('invalid_numeric_value')
    if not math.isfinite(value):raise Invalid('nonfinite_numeric_value')
    return value

def json_value(text):
    def duplicate_check(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise Invalid('duplicate_json_key')
            out[key]=value
        return out
    try:return json.loads(text,object_pairs_hook=duplicate_check,
                          parse_constant=lambda _: (_ for _ in ()).throw(Invalid('nonfinite_json_constant')))
    except json.JSONDecodeError:raise Invalid('malformed_json')

def unwrap_trajectory(data):
    """Use the same trajectory object for geometry and explicit time limits."""
    aliases = {"static": "static", "stationary": "static", "dynamic": "dynamic", "moving": "dynamic"}
    fields = {"trajectory", "points", "positions"}
    wrappers = {"trajectory", "trajectories", "answer", "result", "data", "response"}
    for _ in range(8):
        child = None
        outer_kind = None
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and fields.intersection(data[0]):
            child = dict(data[0])
        elif isinstance(data, dict) and len(data) == 1:
            key, value = next(iter(data.items()))
            key = norm(key)
            if key in wrappers or key in aliases:
                if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
                    value = value[0]
                if isinstance(value, dict) and fields.intersection(value):
                    child = dict(value)
                    outer_kind = aliases.get(key)
        if child is None:
            break
        inner_kind = aliases.get(norm(child.get("type", "")))
        if outer_kind and inner_kind and outer_kind != inner_kind:
            raise Invalid("conflicting_trajectory_wrapper_types")
        if outer_kind and not inner_kind:
            child["type"] = outer_kind
        data = child
    return data

def source_name(text):
    value=norm(text).strip(' .?,;:\"\'')
    previous=None
    while value!=previous:
        previous=value
        value=re.sub(r'^(?:the\s+)?(?:sound\s+|audio\s+)?source\s+(?:of\s+|described as\s+|saying\s+)?','',value).strip(' .?,;:\"\'')
        value=re.sub(r'^the\s+','',value)
    if value in {'sound source','source','another sound source','audio source',''}:return '__unspecified_source__'
    return value

def protect_quotes(text):
    quotes=[]
    def replace(match):
        quotes.append(match.group())
        return f'QUOTED_SOURCE_{len(quotes)-1}'
    masked=re.sub(r'"[^"]*"',replace,text)
    def restore(value):
        for index,quote in enumerate(quotes):value=re.sub(f'QUOTED_SOURCE_{index}',lambda _:quote,value,flags=re.I)
        return value
    return masked,restore

def question_schema(category,question):
    q=clean_question(question)
    low=norm(re.sub(r'"[^"]*"','SOURCE',q))
    schema={'category':category}
    if category in CAT_AXES:
        axis=CAT_AXES[category]
        hits=[x for x in axis if re.search(r'\b(?:'+LABELS[x]+r')\b',low)]
        schema['allowed_labels']=list(axis)
        schema['yes_no_proposition']=hits[0] if len(hits)==1 and ' or ' not in low else None
    if category in {'json_coords','trajectory'}:schema['required_axes']=list(AXES)
    if category=='movement_direction':
        requested=[]
        if re.search(r'left[- ]right|horizontal|lateral',low):requested.append('lateral')
        if re.search(r'vertical|higher or lower|above or below',low):requested.append('vertical')
        if re.search(r'distance change|closer or farther|closer or further|nearer or farther',low):requested.append('distance_change')
        schema['required_axes']=requested or list(MOVEMENT_AXES)
        schema['axis_rule']='explicit_axis_query' if requested else 'complete_end_position_relative_to_start'
    if category=='trajectory':
        m=re.search(r'every\s+('+NUM+r')\s*second',low)
        schema['interval_s']=number(m.group(1)) if m else None
    if category in {'relative_position','comparison'}:
        masked,restore=protect_quotes(q)
        if category=='comparison':
            m=re.search(r'^Between\s+(.+?)\s+and\s+(.+),\s*which one is closer',masked,re.I)
            schema['axis']='distance'
        else:
            patterns=[('lateral',r',\s*is\s+(.+)\s+to the left or right of\s+(.+)$'),
                      ('vertical',r',\s*is\s+(.+)\s+(?:higher or lower|above or below) than\s+(.+)$'),
                      ('depth',r',\s*is\s+(.+)\s+(?:closer or (?:farther|further)) (?:in depth )?than\s+(.+)$')]
            m=None
            for axis,pattern in patterns:
                m=re.search(pattern,masked,re.I)
                if m:schema['axis']=axis;break
        if not m:raise Invalid('query_source_pair_not_identifiable')
        pair=[source_name(restore(m.group(1))),source_name(restore(m.group(2)))]
        if pair[0]==pair[1]:raise Invalid('query_source_pair_indistinguishable')
        schema['source_pair']=pair
    return schema

def scalar(text,category):
    key=SCALARS[category]
    stripped=text.strip()
    if stripped.startswith('{'):
        data=json_value(stripped)
        if not isinstance(data,dict) or key not in data:raise Invalid('required_scalar_missing')
        value=number(data[key])
    else:
        low=norm(stripped)
        if re.search(r'\b(?:nan|inf|infinity)\b',low):raise Invalid('nonfinite_numeric_value')
        if re.search(r'\b(?:there (?:is|are)|sound sources|sound objects|sound emitters|turn left|turn right)\b',low):raise Invalid('cross_task_numeric_claim')
        numbers=re.findall(NUM,low)
        if len(numbers)!=1:raise Invalid('scalar_requires_one_unambiguous_number')
        # A movement displacement is not absolute range or azimuth.
        if re.search(r'relative to|closer|farther|further|to the (?:left|right)',low):raise Invalid('relative_displacement_is_not_absolute_geometry')
        value=number(numbers[0])
        if category=='distance':
            if re.search(r'degrees?|radians?|°',low):raise Invalid('wrong_numeric_unit')
            if re.search(r'\b(?:cm|centimeters?|centimetres?)\b',low):value/=100
            elif re.search(r'\b(?:mm|millimeters?|millimetres?)\b',low):value/=1000
            elif re.search(r'\b(?:km|kilometers?|kilometres?)\b',low):value*=1000
        else:
            if re.search(r'\b(?:m|metres?|meters?|cm|mm|km)\b',low):raise Invalid('wrong_numeric_unit')
            if re.search(r'\bradians?\b',low):value=math.degrees(value)
    if category=='distance' and value<0:raise Invalid('negative_distance')
    if category=='elevation' and not -90<=value<=90:raise Invalid('elevation_outside_physical_range')
    return value

def exact_count(text):
    low=norm(text).strip(' .!?')
    words={'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
    if low in words:return words[low]
    if re.fullmatch(r'\d+',low):return int(low)
    candidates=re.findall(r'\b(?:there (?:is|are) (?:exactly )?)?(\d+|'+ '|'.join(words)+r')\s+(?:(?:distinct|active|sound|audio)\s+)*(?:sources?|objects?|emitters?)\b',low)
    if len(candidates)!=1:raise Invalid('count_requires_one_integer_claim')
    # Do not turn 1.5 sound sources into 5, or ranges into their endpoint.
    if re.search(r'\d\.\d|\d\s*[-–]\s*\d|\bor\b|approximately|about',low):raise Invalid('nonexact_count_claim')
    return words[candidates[0]] if candidates[0] in words else int(candidates[0])

def directional_labels(text,allowed):
    low=norm(re.sub(r'"[^"]*"','SOURCE',text))
    if re.search(r'\b(?:maybe|possibly|either)\b',low):raise Invalid('ambiguous_direction')
    hits=[]
    for label in allowed:
        pattern=r'\b(?:'+LABELS[label]+r')\b'
        for match in re.finditer(pattern,low):
            if re.search(r'\bnot\s+(?:to the\s+|on the\s+|in\s+)?$',low[:match.start()]):continue
            hits.append(label)
    hits=set(hits)
    if len(hits)!=1:raise Invalid('direction_requires_one_axis_label')
    return hits.pop()

def lateral(text,category):
    low=norm(text)
    if re.search(r'already (?:directly )?(?:facing|looking|aligned)|directly facing|straight ahead',low):return 'front'
    if re.search(r'turn (?:around|back)|180\s*(?:°|degrees?)',low):return 'back'
    if re.fullmatch(NUM+r'\s*(?:°|degrees?|deg)[.!]?',low) or (category=='navigation' and re.fullmatch(NUM,low)):
        angle=(number(re.search(NUM,low).group())+180)%360-180
        if abs(angle)<1e-9:return 'front'
        if abs(abs(angle)-180)<1e-9:return 'back'
        return 'left' if angle>0 else 'right'
    if category=='lateral' and re.search(r'relative to.*(?:start|origin|previous|initial)',low):raise Invalid('motion_relation_is_not_listener_side')
    return directional_labels(text,('left','right','front','back'))

def movement_status(text):
    low=norm(text)
    if re.search(r'\bnot stationary\b',low):return 'moving'
    stationary=r'not moving|isn.t moving|did not move|does not move|doesn.t move|no movement|no motion|not in motion|no change in position|same position|similar position|fixed position|at rest|stays in (?:one|the same) (?:place|position)|does not change (?:its )?(?:position|location)|doesn.t change (?:its )?(?:position|location)'
    explicit_still=bool(re.search(stationary,low))
    remaining=re.sub(stationary,'',low)
    still=explicit_still or bool(re.search(r'\bstationary\b|\bstatic\b|\bstill\b',remaining))
    moving=bool(re.search(r'\bmoving\b|\bdynamic\b|in motion|there is motion|has motion|motion associated|chang(?:es|ing) (?:its )?(?:position|location)|(?:position|location) (?:changes|varies)|moves around',remaining))
    if re.search(r'relative to (?:its |the )?(?:previous|start|initial|earlier)',low):
        displacements=re.findall('('+NUM+r')\s*m(?:eters?)?\s*(?:to the (?:left|right)|closer|farther|further|higher|lower)',low)
        moving=moving or any(abs(number(x))>0 for x in displacements)
    if still==moving:raise Invalid('moving_stationary_claim_missing_or_conflicting')
    return 'stationary' if still else 'moving'

def coordinates(data):
    if not isinstance(data,dict):raise Invalid('coordinate_object_required')
    out={}
    if all(k in data for k in AXES):out={k:number(data[k]) for k in AXES}
    elif all(k in data for k in ('x_m','y_m','z_m')):
        x,y,z=[number(data[k]) for k in ('x_m','y_m','z_m')]
        r=math.sqrt(x*x+y*y+z*z)
        if r==0:raise Invalid('zero_radius_has_undefined_angles')
        out=dict(zip(AXES,[math.degrees(math.atan2(y,x)),math.degrees(math.asin(z/r)),r]))
    else:raise Invalid('missing_required_coordinate_axis')
    if out['distance_m']<0 or not -90<=out['elevation_deg']<=90:raise Invalid('coordinates_outside_physical_range')
    return out

def movement(text,schema):
    low=norm(text)
    if re.search(r'did not move|not moving|same position|no change in position|remained stationary',low):return {k:'similar' for k in schema['required_axes']}
    patterns={'lateral':{'left':r'to the left|leftward','right':r'to the right|rightward','similar':r'similar (?:lateral|horizontal)|same (?:lateral|horizontal)'},
              'distance_change':{'closer':r'closer|nearer|approach','farther':r'farther|further|away','similar':r'similar distance|same distance|constant distance'},
              'vertical':{'higher':r'higher|upward','lower':r'lower|downward','similar':r'similar height|same height|constant height'}}
    out={}
    for axis in schema['required_axes']:
        found=[label for label,pattern in patterns[axis].items() if re.search(pattern,low)]
        if len(found)!=1:raise Invalid('missing_or_conflicting_requested_movement_axis:'+axis)
        out[axis]=found[0]
    return out

RELATIONS=[('lateral','left',r'to the left of'),('lateral','right',r'to the right of'),
           ('lateral','similar',r'at (?:a )?similar (?:horizontal|lateral) position to'),
           ('vertical','higher',r'higher than|above'),('vertical','lower',r'lower than|below'),
           ('vertical','similar',r'at (?:a )?similar height to'),
           ('depth','closer',r'closer in depth than'),('depth','farther',r'(?:further|farther) in depth than'),
           ('depth','similar',r'at (?:a )?similar depth to')]

INVERSE={'left':'right','right':'left','higher':'lower','lower':'higher','closer':'farther','farther':'closer','similar':'similar'}

def pair_index(mention,pair):
    named=source_name(mention)
    if named in pair:return pair.index(named)
    if named in {'first','former','first source','former source'}:return 0
    if named in {'second','latter','second source','latter source'}:return 1
    raise Invalid('asserted_source_identity_not_in_query')

def relative(text,schema):
    low=norm(text).strip(' .!?')
    low,restore=protect_quotes(low)
    axis=schema['axis'];pair=schema['source_pair']
    bare={'lateral':{'left':'left','right':'right','same':'similar','similar':'similar'},
          'vertical':{'higher':'higher','up':'higher','above':'higher','lower':'lower','down':'lower','below':'lower','same':'similar','similar':'similar'},
          'depth':{'closer':'closer','nearer':'closer','farther':'farther','further':'farther','same':'similar','similar':'similar'}}
    if low in bare[axis]:return {'source_pair':pair,'axis':axis,'relation':bare[axis][low]}
    matches=[]
    for rel_axis,label,pattern in RELATIONS:
        for m in re.finditer(r'\b(?:'+pattern+r')\b',low):matches.append((rel_axis,label,m))
    if len(matches)!=1:raise Invalid('relation_missing_or_ambiguous')
    rel_axis,label,m=matches[0]
    if rel_axis!=axis:raise Invalid('reference_or_response_answers_wrong_relation_axis')
    subject=re.sub(r'\s+is\s*$','',low[:m.start()]).strip()
    obj=low[m.end():].strip()
    si=pair_index(restore(subject),pair);oi=pair_index(restore(obj),pair)
    if si==oi:raise Invalid('self_relation_or_missing_second_source')
    if si==1:label=INVERSE[label]
    return {'source_pair':pair,'axis':axis,'relation':label}

def comparison(text,schema):
    low=norm(text).strip(' .!?');pair=schema['source_pair']
    if re.search(r'closer in depth|farther in depth|further in depth|similar depth',low):raise Invalid('depth_relation_does_not_identify_radial_distance_comparison')
    if re.search(r'\bequidistant\b|same distance|equal distance',low):return {'source_pair':pair,'closer':'same'}
    m=re.match(r'(.+?)\s+is\s+(?:at (?:a )?)?(?:closer|nearer)(?:\s+distance)?(?:\s+to the listener)?\.?$',low)
    if m:return {'source_pair':pair,'closer':pair_index(m.group(1),pair)}
    m=re.match(r'(.+?)\s+is\s+(farther|further)\s+(?:from the listener )?than\s+(.+)$',low)
    if m:
        si=pair_index(m.group(1),pair);oi=pair_index(m.group(3),pair)
        if si==oi:raise Invalid('self_comparison')
        return {'source_pair':pair,'closer':oi}
    return {'source_pair':pair,'closer':pair_index(low,pair)}

def trajectory(text,schema):
    data=json_value(text)
    if isinstance(data,list):kind=None;points=data;interval=None
    elif isinstance(data,dict):
        kind=data.get('type');interval=data.get('interval_s')
        points=data.get('trajectory')
        if points is None and kind=='static' and isinstance(data.get('position'),dict):points=[data['position']]
        if points is None and kind=='static' and all(k in data for k in AXES):points=[{k:data[k] for k in AXES}]
    else:raise Invalid('trajectory_json_required')
    if not isinstance(points,list) or not points:raise Invalid('empty_trajectory')
    if not all(isinstance(p,dict) for p in points):raise Invalid('trajectory_point_object_required')
    if kind is None:kind='static' if len(points)==1 and 'time_s' not in points[0] else 'dynamic'
    if kind not in {'static','dynamic'}:raise Invalid('unknown_trajectory_type')
    if kind=='static' and len(points)!=1:raise Invalid('static_trajectory_requires_one_position')
    converted=[]
    for p in points:
        point=coordinates(p)
        if 'time_s' in p:point['time_s']=number(p['time_s'])
        elif kind=='dynamic':raise Invalid('dynamic_trajectory_missing_time')
        converted.append(point)
    if kind=='dynamic':
        times=[p['time_s'] for p in converted]
        if any(t<0 for t in times):raise Invalid('negative_trajectory_time')
        if interval is not None:
            interval=number(interval)
            if interval<=0:raise Invalid('nonpositive_trajectory_interval')
    return {'type':kind,'points':converted,'interval_s':interval,
            'source':data.get('source') if isinstance(data,dict) else None}

def point_at_time(value,time):
    if value['type']=='static':return value['points'][0]
    candidates=[(abs(p['time_s']-time),p) for p in value['points'] if abs(p['time_s']-time)<=TIME_TOLERANCE_S]
    if not candidates:raise Invalid('missing_required_trajectory_time')
    nearest=min(delta for delta,_ in candidates)
    closest=[p for delta,p in candidates if abs(delta-nearest)<=1e-9]
    # Duplicate same-time observations can merge only when every coordinate
    # agrees exactly. Never choose a point using its agreement with gold.
    signatures={tuple(p[k] for k in AXES) for p in closest}
    if len(signatures)!=1:raise Invalid('conflicting_coordinates_at_required_trajectory_time')
    return closest[0]

def extract(category,question,raw):
    try:
        schema=question_schema(category,question);text=answer_block(raw)
        if category in SCALARS:value=scalar(text,category)
        elif category=='count':value=exact_count(text)
        elif category in {'lateral','navigation'}:value=lateral(text,category)
        elif category=='movement_status':value=movement_status(text)
        elif category in CAT_AXES:
            proposition=schema.get('yes_no_proposition')
            if proposition and re.fullmatch(r'(?:yes|no)[.!]?',norm(text)):
                value=proposition if norm(text).startswith('yes') else next(x for x in schema['allowed_labels'] if x!=proposition)
            elif category!='cat_near_far' and re.fullmatch(NUM+r'\s*(?:°|degrees?|deg)[.!]?',norm(text)):
                angle=scalar(text,'elevation' if category=='cat_up_down' else 'azimuth')
                projection=angle if category=='cat_up_down' else math.sin(math.radians(angle)) if category=='cat_left_right' else math.cos(math.radians(angle))
                if abs(projection)<1e-8:raise Invalid('angle_on_categorical_axis_boundary')
                value=schema['allowed_labels'][0 if projection>0 else 1]
            else:value=directional_labels(text,schema['allowed_labels'])
        elif category=='json_coords':value=coordinates(json_value(text))
        elif category=='movement_direction':value=movement(text,schema)
        elif category=='relative_position':value=relative(text,schema)
        elif category=='comparison':value=comparison(text,schema)
        elif category=='trajectory':value=trajectory(text,schema)
        else:raise Invalid('unsupported_category')
        return {'ok':True,'value':value,'schema':schema,'reason':None}
    except Invalid as exc:
        reason=str(exc)
        if category in {'azimuth','elevation'} and norm(raw).strip(' .!?') in {'up','down','left','right','above','below'}:
            reason='coarse_reference_or_response_for_numeric_question'
        return {'ok':False,'value':None,'reason':reason}

def coordinate_errors(pred,ref):
    return {'azimuth_deg':abs((pred['azimuth_deg']-ref['azimuth_deg']+180)%360-180),
            'elevation_deg':abs(pred['elevation_deg']-ref['elevation_deg']),
            'distance_m':abs(pred['distance_m']-ref['distance_m'])}

def score_claim(category,pred,ref):
    if not ref['ok']:return {'success':0,'reason':'reference_inadequate'}
    if not pred['ok']:return {'success':0,'reason':'response_unparseable'}
    p=pred['value'];r=ref['value']
    if category in SCALARS:
        key=SCALARS[category]
        error=abs((p-r+180)%360-180) if category=='azimuth' else abs(p-r)
        return {'success':int(error<=THRESHOLDS[key]),'errors':{key:error}}
    if category=='json_coords':
        errors=coordinate_errors(p,r)
        return {'success':int(all(errors[k]<=THRESHOLDS[k] for k in AXES)),'errors':errors}
    if category=='trajectory':
        required=r.get('required_points',r['points'])
        point_scores=[]
        used_times=set()
        for rp in required:
            try:pp=point_at_time(p,rp.get('time_s',0.0))
            except Invalid:point_scores.append(False);continue
            if p['type'] == 'dynamic':
                matched_time=pp['time_s']
                if matched_time in used_times:
                    point_scores.append(False)
                    continue
                used_times.add(matched_time)
            errors=coordinate_errors(pp,rp)
            point_scores.append(all(errors[k]<=THRESHOLDS[k] for k in AXES))
        complete=all(point_scores)
        return {'success':int(complete),'reference_points':len(required),'successful_points':sum(point_scores),
                'point_fraction':sum(point_scores)/len(point_scores),'reason':None if complete else 'trajectory_missing_extra_wrong_time_or_wrong_coordinate'}
    return {'success':int(p==r)}
