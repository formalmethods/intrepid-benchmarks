function replay_counterexamples(cocosim, model_path, mats, cases_path, out)
% REPLAY_COUNTEREXAMPLES  Simulates a model on counterexamples, and says
% where each one's property fails in Simulink. Writes out, a JSON file: the
% step size, and for each case its id and the first step at which the
% input of its property block is false (-1 if at none), or its error.
%
%   replay_counterexamples(cocosim, model_path, mats, cases_path, out)
%
% cases_path is a JSON file, a list of cases: {id, property, steps,
% inputs}, property the path of an Assertion or Proof Objective block
% without the model's name (newlines as spaces), inputs an object from the
% names of the root Inports (name[i] for the element i of a vector, from 1)
% to their values at each step. An input a case does not give is 0.
% cocosim and mats as for sldv_prove. The model is closed without saving:
% the Assertion blocks do not stop the simulation, the input of each
% property block is logged, and a model saved with a variable step solver
% is simulated with a fixed step discrete one, of its own sample time, as
% is one of an automatic fixed step: the step as compiled. An
% object specials, from names to a code at each step (1 +inf, 2 -inf, 3
% NaN, 0 the value of inputs), gives the values an Inport cannot take.
result = struct('model', model_path, 'cases', {{}});
try
    addpath(genpath(fullfile(cocosim, 'libs')));
    for i = 1:numel(mats)
        evalin('base', sprintf('load(''%s'')', strrep(mats{i}, '''', '''''')));
    end
    load_system(model_path);
    [~, model, ~] = fileparts(model_path);
    cases = jsondecode(fileread(cases_path));
    if isstruct(cases)
        cases = num2cell(cases);
    end
    if ~strcmp(get_param(model, 'SolverType'), 'Fixed-step')
        set_param(model, 'SolverType', 'Fixed-step', 'Solver', 'FixedStepDiscrete', 'FixedStep', 'auto');
    end

    % The property blocks, by their path as the cases give it
    blocks = [find_system(model, 'LookUnderMasks', 'all', 'BlockType', 'Assertion'); ...
              find_system(model, 'LookUnderMasks', 'all', 'MaskType', 'Design Verifier Proof Objective')];
    paths = cell(size(blocks));
    for i = 1:numel(blocks)
        if strcmp(get_param(blocks{i}, 'BlockType'), 'Assertion')
            set_param(blocks{i}, 'StopWhenAssertionFail', 'off');
        end
        full = strrep(getfullname(blocks{i}), sprintf('\n'), ' ');
        paths{i} = full(numel(model) + 2:end);
    end
    % the input of each, logged where it comes from
    sources = zeros(size(blocks));
    for i = 1:numel(blocks)
        ports = get_param(blocks{i}, 'PortHandles');
        line = get_param(ports.Inport(1), 'Line');
        sources(i) = get_param(line, 'SrcPortHandle');
        set_param(sources(i), 'DataLogging', 'on');
    end
    set_param(model, 'SignalLogging', 'on', 'SignalLoggingName', 'replay_logsout', ...
              'SaveOutput', 'off', 'SaveState', 'off', 'SaveFinalState', 'off');

    % The root Inports, in the order of their ports, with their widths and
    % types, and the step, as compiled
    inports = find_system(model, 'SearchDepth', 1, 'BlockType', 'Inport');
    numbers = cellfun(@(b) str2double(get_param(b, 'Port')), inports);
    [~, order] = sort(numbers);
    inports = inports(order);
    feval(model, [], [], [], 'compile');
    try
        widths = zeros(size(inports));
        types = cell(size(inports));
        for i = 1:numel(inports)
            dims = get_param(inports{i}, 'CompiledPortDimensions');
            widths(i) = prod(dims.Outport(2:end));
            kinds = get_param(inports{i}, 'CompiledPortDataTypes');
            types{i} = kinds.Outport{1};
        end
        times = Simulink.BlockDiagram.getSampleTimes(model);
        step = inf;
        for i = 1:numel(times)
            value = times(i).Value;
            if isnumeric(value) && numel(value) >= 1 && isfinite(value(1)) && value(1) > 0
                step = min(step, value(1));
            end
        end
    catch e
        feval(model, [], [], [], 'term');
        rethrow(e);
    end
    feval(model, [], [], [], 'term');
    if ~isfinite(step)
        error('replay:step', 'no discrete sample time in %s', model);
    end
    result.step = step;
    % the step as compiled, kept: an automatic one depends on the stop time,
    % which each case changes
    set_param(model, 'FixedStep', sprintf('%.17g', step));

    % An Inport takes finite values only: where a case gives one infinite
    % or NaN, the Inport's lines come from a Switch between it and P.*P -
    % Q.*Q instead, of P and Q realmax or 0 from From Workspace blocks
    keys = cell(size(inports));
    special = false(size(inports));
    for i = 1:numel(inports)
        name = strrep(get_param(inports{i}, 'Name'), sprintf('\n'), ' ');
        keys{i} = cell(1, widths(i));
        for k = 1:widths(i)
            keys{i}{k} = name;
            if widths(i) > 1
                keys{i}{k} = sprintf('%s[%d]', name, k);
            end
            keys{i}{k} = matlab.lang.makeValidName(keys{i}{k});
            for c = 1:numel(cases)
                special(i) = special(i) || (isfield(cases{c}, 'specials') && isfield(cases{c}.specials, keys{i}{k}));
            end
        end
        if special(i)
            if ~any(strcmp(types{i}, {'double', 'single'}))
                error('replay:special', 'an infinite or NaN value for %s, of type %s', name, types{i});
            end
            injectSpecials(model, inports{i}, i, step);
        end
    end

    replayed = cell(1, numel(cases));
    for c = 1:numel(cases)
        one = cases{c};
        entry = struct('id', one.id);
        try
            which = find(strcmp(paths, one.property), 1);
            if isempty(which)
                error('replay:property', 'no Assertion or Proof Objective block %s', one.property);
            end
            steps = double(one.steps);
            in = struct('time', (0:steps - 1)' * step, 'signals', struct('values', {}, 'dimensions', {}));
            given = one.inputs;
            for i = 1:numel(inports)
                values = zeros(steps, widths(i));
                codes = zeros(steps, widths(i));
                for k = 1:widths(i)
                    field = keys{i}{k};
                    if isfield(given, field)
                        values(:, k) = double(given.(field));
                    end
                    if isfield(one, 'specials') && isfield(one.specials, field)
                        codes(:, k) = double(one.specials.(field));
                    end
                end
                if special(i)
                    big = realmax(types{i});
                    times = (0:steps - 1)' * step;
                    p = cast(big * (codes == 1 | codes == 3), types{i});
                    q = cast(big * (codes == 2 | codes == 3), types{i});
                    f = double(codes == 0);
                    assignin('base', sprintf('replay_p_%d', i), struct('time', times, 'signals', struct('values', p, 'dimensions', widths(i))));
                    assignin('base', sprintf('replay_q_%d', i), struct('time', times, 'signals', struct('values', q, 'dimensions', widths(i))));
                    assignin('base', sprintf('replay_f_%d', i), struct('time', times, 'signals', struct('values', f, 'dimensions', widths(i))));
                end
                if strcmp(types{i}, 'boolean')
                    in.signals(i).values = values ~= 0;
                else
                    in.signals(i).values = cast(values, types{i});
                end
                in.signals(i).dimensions = widths(i);
            end
            assignin('base', 'replay_in', in);
            simulated = sim(model, 'StopTime', sprintf('%.17g', (steps - 1) * step), ...
                            'LoadExternalInput', 'on', 'ExternalInput', 'replay_in', ...
                            'ReturnWorkspaceOutputs', 'on');
            logs = simulated.get('replay_logsout');
            [values, at] = logged(logs, sources(which));
            % an Assertion of a vector fails where any element is false
            failing = find(any(values == 0, 2), 1);
            if isempty(failing)
                entry.step = -1;
            else
                entry.step = round(at(failing) / step);
            end
        catch e
            entry.error = getReport(e, 'basic', 'hyperlinks', 'off');
        end
        replayed{c} = entry;
    end
    result.cases = replayed;
catch e
    result.error = getReport(e, 'extended', 'hyperlinks', 'off');
end
bdclose('all');
write_json(out, result);
end

function injectSpecials(model, inport, i, step)
% Between the root Inport i and where it goes, a Switch: the Inport where
% replay_f_i is not 0, else replay_p_i.*replay_p_i - replay_q_i.*replay_q_i
ports = get_param(inport, 'PortHandles');
line = get_param(ports.Outport(1), 'Line');
destinations = get_param(line, 'DstPortHandle');
delete_line(line);
period = sprintf('%.17g', step);
blocks = struct();
for which = {'p', 'q', 'f'}
    path = sprintf('%s/replay_%s_%d', model, which{1}, i);
    add_block('simulink/Sources/From Workspace', path, 'VariableName', sprintf('replay_%s_%d', which{1}, i), ...
              'SampleTime', period, 'Interpolate', 'off', 'OutputAfterFinalValue', 'Holding final value');
    blocks.(which{1}) = get_param(path, 'PortHandles');
end
square = {};
for which = {'p', 'q'}
    path = sprintf('%s/replay_%s%s_%d', model, which{1}, which{1}, i);
    add_block('simulink/Math Operations/Product', path, 'Inputs', '**', 'Multiplication', 'Element-wise(.*)');
    handles = get_param(path, 'PortHandles');
    add_line(model, blocks.(which{1}).Outport(1), handles.Inport(1));
    add_line(model, blocks.(which{1}).Outport(1), handles.Inport(2));
    square{end + 1} = handles; %#ok<AGROW>
end
path = sprintf('%s/replay_special_%d', model, i);
add_block('simulink/Math Operations/Sum', path, 'Inputs', '+-');
difference = get_param(path, 'PortHandles');
add_line(model, square{1}.Outport(1), difference.Inport(1));
add_line(model, square{2}.Outport(1), difference.Inport(2));
path = sprintf('%s/replay_switch_%d', model, i);
add_block('simulink/Signal Routing/Switch', path, 'Criteria', 'u2 ~= 0');
switched = get_param(path, 'PortHandles');
add_line(model, ports.Outport(1), switched.Inport(1));
add_line(model, blocks.f.Outport(1), switched.Inport(2));
add_line(model, difference.Outport(1), switched.Inport(3));
for d = 1:numel(destinations)
    add_line(model, switched.Outport(1), destinations(d));
end
end

function [values, at] = logged(logs, source)
% The values of the signal logged at the output port source, a row per
% time logged, its elements side by side; and those times
block = getfullname(get_param(source, 'Parent'));
port = get_param(source, 'PortNumber');
for i = 1:logs.numElements
    element = logs.getElement(i);
    if strcmp(element.BlockPath.getBlock(element.BlockPath.getLength()), block) && element.PortIndex == port
        at = element.Values.Time;
        data = element.Values.Data;
        if size(data, 1) ~= numel(at) || (numel(at) == 1 && ndims(data) > 2)
            % the time last, as for a matrix signal
            data = permute(data, [ndims(data), 1:ndims(data) - 1]);
        end
        values = reshape(data, numel(at), []);
        return;
    end
end
error('replay:log', 'the input of the property, from %s, is not logged', block);
end
