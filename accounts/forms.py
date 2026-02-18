from django import forms
from django.utils.safestring import mark_safe
from django.utils.html import escape
import json
from .models import SiteSetting


class PolicyBlockWidget(forms.Widget):
    """Custom widget for rendering policy blocks with a user-friendly interface"""
    
    def __init__(self, field_name='policy', attrs=None):
        self.field_name = field_name
        super().__init__(attrs)
    
    def render(self, name, value, attrs=None, renderer=None):
        # Parse existing blocks from JSON
        blocks = []
        if value:
            try:
                if isinstance(value, str):
                    blocks = json.loads(value)
                else:
                    blocks = value
                if not isinstance(blocks, list):
                    blocks = []
            except (json.JSONDecodeError, TypeError):
                blocks = []
        
        # Build blocks HTML
        blocks_html = ''
        if blocks:
            for idx, block in enumerate(blocks):
                title = escape(block.get('title', ''))
                items = block.get('items', [])
                items_text = escape('\n'.join(items)) if items else ''
                footer = escape(block.get('footer', ''))
                
                is_first = idx == 0
                is_last = idx == len(blocks) - 1
                preview = title if title else '(Untitled block)'
                
                blocks_html += f'''
                <div class="policy-block collapsed" data-index="{idx}">
                    <div class="block-header" onclick="this.parentElement.classList.toggle('collapsed')">
                        <div class="block-header-left">
                            <span class="collapse-icon">▶</span>
                            <span class="block-number">#{idx + 1}</span>
                            <span class="block-preview">{preview}</span>
                        </div>
                        <div class="block-controls" onclick="event.stopPropagation()">
                            <button type="button" class="move-up-btn" title="Move Up" {"disabled" if is_first else ""}>↑</button>
                            <button type="button" class="move-down-btn" title="Move Down" {"disabled" if is_last else ""}>↓</button>
                            <button type="button" class="remove-block-btn" title="Remove">✕</button>
                        </div>
                    </div>
                    <div class="block-fields">
                        <div class="fields-grid">
                            <div class="field-cell full">
                                <label>Title</label>
                                <input type="text" name="{name}_block_{idx}_title" value="{title}" placeholder="Section title" class="title-input">
                            </div>
                            <div class="field-cell full">
                                <label>List Items <small>(one per line)</small></label>
                                <textarea name="{name}_block_{idx}_items" rows="3" placeholder="Item 1&#10;Item 2&#10;Item 3">{items_text}</textarea>
                            </div>
                            <div class="field-cell full">
                                <label>Footer</label>
                                <textarea name="{name}_block_{idx}_footer" rows="2" placeholder="Closing text (optional)">{footer}</textarea>
                            </div>
                        </div>
                    </div>
                </div>
                '''
        else:
            blocks_html = '<p class="no-blocks-message">No blocks yet. Click "+ Add Block" to create one.</p>'
        
        html = f'''
        <div class="policy-blocks-container" data-field-name="{name}">
            <div class="blocks-list" id="{name}_blocks">
                {blocks_html}
            </div>
            <button type="button" class="add-block-btn" data-field="{name}">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                Add Block
            </button>
        </div>
        
        <style>
        .policy-blocks-container {{
            border: 1px solid rgb(229 231 235);
            border-radius: 0.5rem;
            padding: 1rem;
            background: rgb(249 250 251);
        }}
        .blocks-list {{
            max-height: 600px;
            overflow-y: auto;
            margin-bottom: 0.75rem;
        }}
        .policy-block {{
            background: #fff;
            border: 1px solid rgb(229 231 235);
            border-radius: 0.5rem;
            margin-bottom: 0.5rem;
            box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.05);
            transition: box-shadow 0.15s;
        }}
        .policy-block:hover {{
            box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1);
        }}
        .policy-block.collapsed .block-fields {{
            display: none;
        }}
        .policy-block.collapsed .collapse-icon {{
            transform: rotate(0deg);
        }}
        .policy-block:not(.collapsed) .collapse-icon {{
            transform: rotate(90deg);
        }}
        .block-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem 1rem;
            cursor: pointer;
            background: rgb(249 250 251);
            border-radius: 0.5rem 0.5rem 0 0;
            user-select: none;
            transition: background 0.15s;
        }}
        .policy-block.collapsed .block-header {{
            border-radius: 0.5rem;
        }}
        .block-header:hover {{
            background: rgb(243 244 246);
        }}
        .block-header-left {{
            display: flex;
            align-items: center;
            gap: 0.625rem;
            flex: 1;
            min-width: 0;
        }}
        .collapse-icon {{
            font-size: 10px;
            color: rgb(107 114 128);
            transition: transform 0.15s;
        }}
        .block-number {{
            font-weight: 600;
            color: rgb(14 165 233);
            font-size: 0.75rem;
            flex-shrink: 0;
            background: rgb(240 249 255);
            padding: 0.125rem 0.5rem;
            border-radius: 9999px;
        }}
        .block-preview {{
            color: rgb(55 65 81);
            font-size: 0.875rem;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .block-controls {{
            display: flex;
            gap: 0.25rem;
            flex-shrink: 0;
        }}
        .block-controls button {{
            padding: 0.375rem 0.625rem;
            border: 1px solid rgb(229 231 235);
            border-radius: 0.375rem;
            background: #fff;
            cursor: pointer;
            font-size: 0.75rem;
            color: rgb(107 114 128);
            transition: all 0.15s;
        }}
        .block-controls button:hover:not(:disabled) {{
            background: rgb(14 165 233);
            color: #fff;
            border-color: rgb(14 165 233);
        }}
        .block-controls button:disabled {{
            opacity: 0.4;
            cursor: not-allowed;
        }}
        .remove-block-btn:hover:not(:disabled) {{
            background: rgb(239 68 68) !important;
            border-color: rgb(239 68 68) !important;
        }}
        .block-fields {{
            padding: 1rem;
            border-top: 1px solid rgb(229 231 235);
            background: #fff;
            border-radius: 0 0 0.5rem 0.5rem;
        }}
        .fields-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
        }}
        .field-cell.full {{
            grid-column: 1 / -1;
        }}
        .field-cell label {{
            display: block;
            font-weight: 500;
            margin-bottom: 0.375rem;
            color: rgb(55 65 81);
            font-size: 0.8125rem;
        }}
        .field-cell label small {{
            font-weight: 400;
            color: rgb(156 163 175);
            margin-left: 0.25rem;
        }}
        .field-cell input[type="text"],
        .field-cell textarea {{
            width: 100%;
            padding: 0.5rem 0.75rem;
            border: 1px solid rgb(209 213 219);
            border-radius: 0.375rem;
            font-size: 0.875rem;
            font-family: inherit;
            box-sizing: border-box;
            background: #fff;
            color: rgb(17 24 39);
            transition: border-color 0.15s, box-shadow 0.15s;
        }}
        .field-cell input::placeholder,
        .field-cell textarea::placeholder {{
            color: rgb(156 163 175);
        }}
        .field-cell input:focus,
        .field-cell textarea:focus {{
            border-color: rgb(14 165 233);
            outline: none;
            box-shadow: 0 0 0 3px rgb(14 165 233 / 0.1);
        }}
        .field-cell textarea {{
            resize: vertical;
            min-height: 4.5rem;
        }}
        .add-block-btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            width: 100%;
            padding: 0.625rem 1rem;
            background: rgb(14 165 233);
            color: #fff;
            border: none;
            border-radius: 0.5rem;
            font-size: 0.875rem;
            font-weight: 500;
            cursor: pointer;
            transition: background 0.15s;
        }}
        .add-block-btn:hover {{
            background: rgb(2 132 199);
        }}
        .add-block-btn svg {{
            flex-shrink: 0;
        }}
        .no-blocks-message {{
            text-align: center;
            color: rgb(107 114 128);
            padding: 2rem 1rem;
            font-size: 0.875rem;
        }}
        </style>
        
        <script>
        (function() {{
            function initPolicyBlocks() {{
                document.querySelectorAll('.policy-blocks-container').forEach(function(container) {{
                    if (container.dataset.initialized) return;
                    container.dataset.initialized = 'true';
                    
                    var fieldName = container.dataset.fieldName;
                    var blocksList = container.querySelector('.blocks-list');
                    var addBtn = container.querySelector('.add-block-btn');
                    
                    addBtn.addEventListener('click', function() {{
                        var blocks = blocksList.querySelectorAll('.policy-block');
                        var newIndex = blocks.length;
                        
                        var noBlocksMsg = blocksList.querySelector('.no-blocks-message');
                        if (noBlocksMsg) noBlocksMsg.remove();
                        
                        var blockHtml = createBlockHtml(fieldName, newIndex);
                        var tempDiv = document.createElement('div');
                        tempDiv.innerHTML = blockHtml;
                        var newBlock = tempDiv.firstElementChild;
                        blocksList.appendChild(newBlock);
                        
                        attachBlockListeners(newBlock, fieldName);
                        updateBlockNumbers(blocksList);
                    }});
                    
                    blocksList.querySelectorAll('.policy-block').forEach(function(block) {{
                        attachBlockListeners(block, fieldName);
                    }});
                }});
            }}
            
            function createBlockHtml(fieldName, index) {{
                return '<div class="policy-block" data-index="' + index + '">' +
                    '<div class="block-header" onclick="this.parentElement.classList.toggle(\\'collapsed\\')">' +
                        '<div class="block-header-left">' +
                            '<span class="collapse-icon">▶</span>' +
                            '<span class="block-number">#' + (index + 1) + '</span>' +
                            '<span class="block-preview">(Untitled block)</span>' +
                        '</div>' +
                        '<div class="block-controls" onclick="event.stopPropagation()">' +
                            '<button type="button" class="move-up-btn" title="Move Up">↑</button>' +
                            '<button type="button" class="move-down-btn" title="Move Down">↓</button>' +
                            '<button type="button" class="remove-block-btn" title="Remove">✕</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="block-fields">' +
                        '<div class="fields-grid">' +
                            '<div class="field-cell full">' +
                                '<label>Title</label>' +
                                '<input type="text" name="' + fieldName + '_block_' + index + '_title" value="" placeholder="Section title" class="title-input">' +
                            '</div>' +
                            '<div class="field-cell full">' +
                                '<label>List Items <small>(one per line)</small></label>' +
                                '<textarea name="' + fieldName + '_block_' + index + '_items" rows="3" placeholder="Item 1&#10;Item 2&#10;Item 3"></textarea>' +
                            '</div>' +
                            '<div class="field-cell full">' +
                                '<label>Footer</label>' +
                                '<textarea name="' + fieldName + '_block_' + index + '_footer" rows="2" placeholder="Closing text (optional)"></textarea>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                '</div>';
            }}
            
            function attachBlockListeners(block, fieldName) {{
                var blocksList = block.parentElement;
                
                // Update preview when title changes
                var titleInput = block.querySelector('.title-input');
                var preview = block.querySelector('.block-preview');
                if (titleInput && preview) {{
                    titleInput.addEventListener('input', function() {{
                        preview.textContent = this.value || '(Untitled block)';
                    }});
                }}
                
                block.querySelector('.remove-block-btn').addEventListener('click', function() {{
                    if (confirm('Remove this block?')) {{
                        block.remove();
                        reindexBlocks(blocksList, fieldName);
                        updateBlockNumbers(blocksList);
                        
                        if (blocksList.querySelectorAll('.policy-block').length === 0) {{
                            var msg = document.createElement('p');
                            msg.className = 'no-blocks-message';
                            msg.textContent = 'No blocks yet. Click "+ Add Block" to create one.';
                            blocksList.appendChild(msg);
                        }}
                    }}
                }});
                
                block.querySelector('.move-up-btn').addEventListener('click', function() {{
                    var prev = block.previousElementSibling;
                    if (prev && prev.classList.contains('policy-block')) {{
                        blocksList.insertBefore(block, prev);
                        reindexBlocks(blocksList, fieldName);
                        updateBlockNumbers(blocksList);
                    }}
                }});
                
                block.querySelector('.move-down-btn').addEventListener('click', function() {{
                    var next = block.nextElementSibling;
                    if (next && next.classList.contains('policy-block')) {{
                        blocksList.insertBefore(next, block);
                        reindexBlocks(blocksList, fieldName);
                        updateBlockNumbers(blocksList);
                    }}
                }});
            }}
            
            function reindexBlocks(blocksList, fieldName) {{
                var blocks = blocksList.querySelectorAll('.policy-block');
                blocks.forEach(function(block, index) {{
                    block.dataset.index = index;
                    var fields = ['title', 'items', 'footer'];
                    fields.forEach(function(field) {{
                        var input = block.querySelector('[name*="_' + field + '"]');
                        if (input) input.name = fieldName + '_block_' + index + '_' + field;
                    }});
                }});
            }}
            
            function updateBlockNumbers(blocksList) {{
                var blocks = blocksList.querySelectorAll('.policy-block');
                var total = blocks.length;
                blocks.forEach(function(block, index) {{
                    block.querySelector('.block-number').textContent = '#' + (index + 1);
                    var upBtn = block.querySelector('.move-up-btn');
                    var downBtn = block.querySelector('.move-down-btn');
                    upBtn.disabled = (index === 0);
                    downBtn.disabled = (index === total - 1);
                }});
            }}
            
            if (document.readyState === 'loading') {{
                document.addEventListener('DOMContentLoaded', initPolicyBlocks);
            }} else {{
                initPolicyBlocks();
            }}
        }})();
        </script>
        '''
        
        return mark_safe(html)
    
    def value_from_datadict(self, data, files, name):
        """Convert form data back to JSON"""
        blocks = []
        
        # Find all block indices
        block_indices = set()
        prefix = f"{name}_block_"
        for key in data.keys():
            if key.startswith(prefix):
                # Extract block index from key like "privacy_policy_block_0_title"
                parts = key[len(prefix):].split('_')
                if parts[0].isdigit():
                    block_indices.add(int(parts[0]))
        
        # Sort indices and build blocks
        for idx in sorted(block_indices):
            title = data.get(f"{name}_block_{idx}_title", "").strip()
            items_text = data.get(f"{name}_block_{idx}_items", "").strip()
            footer = data.get(f"{name}_block_{idx}_footer", "").strip()
            
            # Parse items (one per line)
            items = [item.strip() for item in items_text.split('\n') if item.strip()]
            
            # Only add block if it has some content
            if title or items or footer:
                block = {}
                if title:
                    block['title'] = title
                if items:
                    block['items'] = items
                if footer:
                    block['footer'] = footer
                blocks.append(block)
        
        return json.dumps(blocks)


class SiteSettingForm(forms.ModelForm):
    """Custom form for SiteSetting with block-based policy editors"""
    
    class Meta:
        model = SiteSetting
        fields = '__all__'
        widgets = {
            'privacy_policy': PolicyBlockWidget(field_name='privacy_policy'),
            'terms_of_service': PolicyBlockWidget(field_name='terms_of_service'),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Convert dict to list if needed for initial display
        for field_name in ['privacy_policy', 'terms_of_service']:
            if field_name in self.initial:
                value = self.initial[field_name]
                if isinstance(value, dict) and not value:
                    self.initial[field_name] = []
