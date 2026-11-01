package gov.takx.api.plugin.annotations;
import java.lang.annotation.*;
@Target(ElementType.TYPE) @Retention(RetentionPolicy.RUNTIME)
public @interface AppPlugin { PluginSpecification spec(); PluginDescriptor descriptor();
  gov.takx.api.plugin.app.IAppPluginManager.AutoStartMode autoStartMode() default gov.takx.api.plugin.app.IAppPluginManager.AutoStartMode.none; }
